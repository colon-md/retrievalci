"""Amazon Bedrock Knowledge Bases adapter — second hosted Mode A target.

Implements HostedSystem against Bedrock Knowledge Bases on OpenSearch
Serverless. The lifecycle has more moving parts than Vertex AI RAG Engine —
Bedrock orchestrates retrieval but doesn't own the vector store, so we
provision OpenSearch Serverless ourselves, plus a service role Bedrock
assumes to read the store + S3 corpus.

Created on each run, torn down at end:
  - IAM role for Bedrock service to assume
  - S3 bucket for corpus storage
  - OpenSearch Serverless: encryption policy + network policy + data access
    policy + collection + vector index
  - Bedrock Knowledge Base + data source + ingestion job

Teardown happens in reverse-creation order so a partial-failure state still
cleans up everything that was actually provisioned.

Cost-safety identical to the Vertex adapter: per-query budget cap, atexit
+ signal handlers, idempotent teardown.

OCU cost: OpenSearch Serverless has a 2-OCU floor at ~$0.24/OCU-hr, so the
binding cost line is wall-clock collection lifetime. Aim for <15 min between
create and delete.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from retrievalci.rag_eval.hosted import (
    IndexHandle,
    RunBudget,
    read_manifest,
    write_manifest,
)
from retrievalci.rag_eval.types import Citation, SystemAnswer

# Cost rates for pre-flight estimation (USD).
_COST_OCU_HOUR = 0.24
_COST_OCU_FLOOR = 2  # OpenSearch Serverless minimum OCUs
_COST_EMBED_PER_DOC = 0.001
_COST_RETRIEVE_PER_QUERY = 0.0001

# Bedrock-managed Cohere embedding model (1024-dim). Titan would also be
# 1024-dim and conventionally preferred, but Bedrock Titan TPM quota is 0
# on freshly-reactivated accounts. Cohere works without quota wrangling.
_EMBEDDING_MODEL_ARN_TEMPLATE = (
    "arn:aws:bedrock:{region}::foundation-model/cohere.embed-english-v3"
)
_EMBEDDING_DIM = 1024


@dataclass
class BedrockKBConfig:
    """Configuration for one Bedrock KB run."""

    region: str = "us-east-1"
    name_prefix: str = "retrievalci-bench-v0"
    bucket_name: str | None = None  # auto-generated if None


@dataclass
class _ProvisionedResources:
    """Inventory of created AWS resources, used in reverse for teardown."""

    bucket: str | None = None
    iam_role_name: str | None = None
    iam_policy_arns: list[str] = field(default_factory=list)
    encryption_policy_name: str | None = None
    network_policy_name: str | None = None
    access_policy_name: str | None = None
    collection_id: str | None = None
    collection_arn: str | None = None
    collection_endpoint: str | None = None
    vector_index_name: str | None = None
    knowledge_base_id: str | None = None
    data_source_id: str | None = None


class BedrockKBSystem:
    """Mode A retrieval via Bedrock Knowledge Bases + OpenSearch Serverless."""

    name = "bedrock_kb"

    def __init__(
        self,
        config: BedrockKBConfig,
        repo_root: Path,
        budget: RunBudget,
        session: boto3.Session,
    ) -> None:
        self._config = config
        self._repo_root = repo_root
        self._budget = budget
        self._session = session
        self._resources = _ProvisionedResources()
        self._index: IndexHandle | None = None
        self._file_uri_to_repo: dict[str, str] = {}

        # Pre-cache clients for clearer cleanup paths.
        self._s3 = session.client("s3", region_name=config.region)
        self._iam = session.client("iam")
        self._aoss = session.client("opensearchserverless", region_name=config.region)
        self._bedrock_agent = session.client("bedrock-agent", region_name=config.region)
        self._bedrock_runtime = session.client(
            "bedrock-agent-runtime", region_name=config.region
        )
        self._sts = session.client("sts")

    # ---- HostedSystem protocol ----

    def index(self, corpus_dir: Path, corpus_version_hash: str) -> IndexHandle:
        short_hash = corpus_version_hash[:8]
        prefix = f"{self._config.name_prefix}-{short_hash}"

        # 1. S3 bucket for corpus files
        self._resources.bucket = self._create_bucket(prefix)
        self._upload_corpus_to_s3(corpus_dir, self._resources.bucket)

        # 2. IAM service role for Bedrock to assume
        self._resources.iam_role_name = f"{prefix}-bedrock-role"
        role_arn = self._create_bedrock_service_role(self._resources)

        # 3. OpenSearch Serverless: policies + collection + vector index
        collection_id, collection_arn, collection_endpoint = self._create_aoss_collection(
            prefix, role_arn
        )
        self._resources.collection_id = collection_id
        self._resources.collection_arn = collection_arn
        self._resources.collection_endpoint = collection_endpoint
        self._wait_for_collection_active(collection_id)
        self._resources.vector_index_name = f"{prefix}-vector-index"
        self._create_vector_index(
            self._resources.collection_endpoint,
            self._resources.vector_index_name,
        )

        # 4. Bedrock Knowledge Base
        kb_id = self._create_knowledge_base(
            prefix, role_arn, self._resources.collection_arn, self._resources.vector_index_name
        )
        self._resources.knowledge_base_id = kb_id

        # 5. Data source pointing at the S3 bucket; trigger ingestion.
        ds_id = self._create_data_source(kb_id, self._resources.bucket)
        self._resources.data_source_id = ds_id
        self._start_and_wait_ingestion(kb_id, ds_id)

        # 6. Persist chunk manifest (S3 URI → repo-relative path).
        write_manifest(
            self._repo_root, self.name, corpus_version_hash, self._file_uri_to_repo
        )
        self._index = IndexHandle(
            provider_index_id=kb_id,
            corpus_version_hash=corpus_version_hash,
        )
        return self._index

    def chunk_manifest(self) -> dict[str, str]:
        if self._index is None:
            return {}
        return read_manifest(self._repo_root, self.name, self._index.corpus_version_hash)

    def estimate_cost(self, n_questions: int) -> float:
        # Assume 1 hour OCU lifetime as the upper bound for the run window.
        return (
            _COST_OCU_HOUR * _COST_OCU_FLOOR  # OCU storage
            + _COST_EMBED_PER_DOC * len(self._file_uri_to_repo or {1: 1})
            + _COST_RETRIEVE_PER_QUERY * n_questions
        )

    def answer(self, question: str) -> SystemAnswer:
        if self._resources.knowledge_base_id is None or self._index is None:
            raise RuntimeError("answer() called before index() — KB not provisioned")
        manifest = self.chunk_manifest()
        t0 = time.perf_counter()
        result = self._bedrock_runtime.retrieve(
            knowledgeBaseId=self._resources.knowledge_base_id,
            retrievalQuery={"text": question},
            retrievalConfiguration={
                "vectorSearchConfiguration": {"numberOfResults": 5},
            },
        )
        latency_ms = (time.perf_counter() - t0) * 1000
        self._budget.record(_COST_RETRIEVE_PER_QUERY)
        self._budget.record_query()
        from retrievalci.rag_eval.hosted import resolve_source_path
        citations: list[Citation] = []
        for hit in result.get("retrievalResults", []):
            location = hit.get("location", {})
            s3_uri = location.get("s3Location", {}).get("uri", "")
            repo_path = resolve_source_path(s3_uri, manifest)
            text = (hit.get("content") or {}).get("text") or ""
            citations.append(Citation(source_path=repo_path, span=text[:160] or None))
        return SystemAnswer(
            answer="",
            citations=(),
            retrieved_sources=tuple(citations),
            latency_ms=latency_ms,
            retrieval_latency_ms=latency_ms,  # Mode A — retrieval is the whole call
            tokens_used=0,
            cost_usd=_COST_RETRIEVE_PER_QUERY,
            corpus_version_hash=self._index.corpus_version_hash,
            index_build_id=self._resources.knowledge_base_id,
            generator_model_id="bedrock-kb-retrieve-only",
            meta={"aoss_top_k": 5},
        )

    def teardown(self) -> None:
        """Idempotent multi-service cleanup. Logs each step; never raises.

        Order matters: KB → DataSource (cascades with KB on delete) →
        Collection → policies → IAM role → S3 bucket. Each step swallows
        exceptions so a partial cleanup-failure doesn't abort the rest.
        """
        r = self._resources

        if r.knowledge_base_id:
            self._safe(
                f"delete KB {r.knowledge_base_id}",
                lambda: self._bedrock_agent.delete_knowledge_base(
                    knowledgeBaseId=r.knowledge_base_id
                ),
            )
            r.knowledge_base_id = None
            r.data_source_id = None  # deleted as cascade

        if r.collection_id:
            self._safe(
                f"delete AOSS collection {r.collection_id}",
                lambda: self._aoss.delete_collection(id=r.collection_id),
            )
            r.collection_id = None
            r.collection_arn = None
            r.collection_endpoint = None
            r.vector_index_name = None

        for policy_type, name in (
            ("data", r.access_policy_name),
            ("network", r.network_policy_name),
            ("encryption", r.encryption_policy_name),
        ):
            if name:
                self._safe(
                    f"delete AOSS {policy_type} policy {name}",
                    lambda n=name, t=policy_type: self._aoss.delete_security_policy(name=n, type=t)
                    if t != "data"
                    else self._aoss.delete_access_policy(name=n, type=t),
                )
        r.access_policy_name = None
        r.network_policy_name = None
        r.encryption_policy_name = None

        if r.iam_role_name:
            for arn in r.iam_policy_arns:
                self._safe(
                    f"detach IAM policy {arn}",
                    lambda a=arn: self._iam.detach_role_policy(
                        RoleName=r.iam_role_name, PolicyArn=a
                    ),
                )
            # Inline policies on the role
            try:
                inline = self._iam.list_role_policies(RoleName=r.iam_role_name)
                for pname in inline.get("PolicyNames", []):
                    self._safe(
                        f"delete inline IAM policy {pname}",
                        lambda p=pname: self._iam.delete_role_policy(
                            RoleName=r.iam_role_name, PolicyName=p
                        ),
                    )
            except ClientError:
                pass
            self._safe(
                f"delete IAM role {r.iam_role_name}",
                lambda: self._iam.delete_role(RoleName=r.iam_role_name),
            )
            r.iam_role_name = None
            r.iam_policy_arns = []

        if r.bucket:
            self._safe(
                f"empty + delete S3 bucket {r.bucket}",
                lambda: self._empty_and_delete_bucket(r.bucket),
            )
            r.bucket = None

    def __enter__(self) -> BedrockKBSystem:
        return self

    def __exit__(self, *exc) -> None:
        self.teardown()

    # ---- provisioning helpers ----

    def _safe(self, label: str, fn) -> None:
        try:
            fn()
            print(f"  teardown: {label} ✓")
        except Exception as e:
            print(f"  teardown: {label} ✗ {type(e).__name__}: {e}")

    def _create_bucket(self, prefix: str) -> str:
        # S3 bucket names are global; use account ID + short uuid for uniqueness.
        account = self._sts.get_caller_identity()["Account"]
        name = f"{prefix}-{account[-6:]}-{uuid.uuid4().hex[:6]}".lower()
        if self._config.region == "us-east-1":
            self._s3.create_bucket(Bucket=name)
        else:
            self._s3.create_bucket(
                Bucket=name,
                CreateBucketConfiguration={"LocationConstraint": self._config.region},
            )
        print(f"  Created S3 bucket: {name}")
        return name

    def _upload_corpus_to_s3(self, corpus_dir: Path, bucket: str) -> None:
        for src in sorted(corpus_dir.glob("*.md")):
            key = f"corpus/{src.name}"
            self._s3.upload_file(str(src), bucket, key)
            repo_relative = str(src.relative_to(self._repo_root))
            s3_uri = f"s3://{bucket}/{key}"
            self._file_uri_to_repo[s3_uri] = repo_relative
            self._budget.record(_COST_EMBED_PER_DOC)
        print(f"  Uploaded {len(self._file_uri_to_repo)} files to s3://{bucket}/")

    def _create_bedrock_service_role(self, r: _ProvisionedResources) -> str:
        # Trust policy: Bedrock service principal can sts:AssumeRole.
        trust = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "bedrock.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }],
        }
        role = self._iam.create_role(
            RoleName=r.iam_role_name,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="RetrievalCI Bedrock KB service role (auto-created, auto-deleted)",
            MaxSessionDuration=3600,
        )
        role_arn = role["Role"]["Arn"]

        # Inline permissions policy: access to S3 (read corpus), Bedrock
        # (invoke embedding model), and OpenSearch Serverless (read/write index).
        perms = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:ListBucket"],
                    "Resource": [
                        f"arn:aws:s3:::{r.bucket}",
                        f"arn:aws:s3:::{r.bucket}/*",
                    ],
                },
                {
                    "Effect": "Allow",
                    "Action": ["bedrock:InvokeModel"],
                    "Resource": _EMBEDDING_MODEL_ARN_TEMPLATE.format(region=self._config.region),
                },
                {
                    "Effect": "Allow",
                    "Action": ["aoss:APIAccessAll"],
                    "Resource": "*",  # tightened by data access policy
                },
            ],
        }
        self._iam.put_role_policy(
            RoleName=r.iam_role_name,
            PolicyName="RetrievalCIBedrockKBInlinePolicy",
            PolicyDocument=json.dumps(perms),
        )
        # Roles take ~10 seconds to propagate in IAM.
        time.sleep(10)
        print(f"  Created IAM role: {r.iam_role_name}")
        return role_arn

    def _create_aoss_collection(self, prefix: str, role_arn: str) -> tuple[str, str, str]:
        # Names must be ≤32 chars and lowercase.
        coll_name = prefix.lower()[:32]
        enc_name = f"{coll_name}-enc"[:32]
        net_name = f"{coll_name}-net"[:32]
        acc_name = f"{coll_name}-acc"[:32]
        principals = [role_arn, self._sts.get_caller_identity()["Arn"]]

        self._aoss.create_security_policy(
            name=enc_name,
            type="encryption",
            policy=json.dumps({
                "Rules": [{"ResourceType": "collection", "Resource": [f"collection/{coll_name}"]}],
                "AWSOwnedKey": True,
            }),
        )
        self._resources.encryption_policy_name = enc_name

        self._aoss.create_security_policy(
            name=net_name,
            type="network",
            policy=json.dumps([{
                "Rules": [
                    {"ResourceType": "collection", "Resource": [f"collection/{coll_name}"]},
                    {"ResourceType": "dashboard", "Resource": [f"collection/{coll_name}"]},
                ],
                "AllowFromPublic": True,
            }]),
        )
        self._resources.network_policy_name = net_name

        self._aoss.create_access_policy(
            name=acc_name,
            type="data",
            policy=json.dumps([{
                "Rules": [
                    {
                        "ResourceType": "collection",
                        "Resource": [f"collection/{coll_name}"],
                        "Permission": ["aoss:*"],
                    },
                    {
                        "ResourceType": "index",
                        "Resource": [f"index/{coll_name}/*"],
                        "Permission": ["aoss:*"],
                    },
                ],
                "Principal": principals,
            }]),
        )
        self._resources.access_policy_name = acc_name

        coll = self._aoss.create_collection(name=coll_name, type="VECTORSEARCH")
        details = coll["createCollectionDetail"]
        print(f"  Created AOSS collection: {coll_name} (id={details['id']})")
        return details["id"], details["arn"], ""  # endpoint filled in by _wait_for_collection_active

    def _wait_for_collection_active(self, collection_id: str) -> None:
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            res = self._aoss.batch_get_collection(ids=[collection_id])
            details = res.get("collectionDetails", [])
            if details:
                status = details[0]["status"]
                if status == "ACTIVE":
                    self._resources.collection_endpoint = details[0]["collectionEndpoint"]
                    print(f"  AOSS collection ACTIVE: {self._resources.collection_endpoint}")
                    return
                if status == "FAILED":
                    raise RuntimeError(f"AOSS collection creation FAILED: {details[0]}")
            time.sleep(5)
        raise TimeoutError(f"AOSS collection {collection_id} did not become ACTIVE within 600s")

    def _create_vector_index(self, endpoint: str, index_name: str) -> None:
        """Create the Bedrock-compatible vector index via the AOSS data plane.

        Uses opensearch-py + AWS4Auth (the canonical AWS-documented path) so
        SigV4 signing matches AOSS's expectations exactly. Hand-rolling SigV4
        via urllib hit a generic 403 with no detail — the OpenSearch client
        is much less finicky.
        """
        from opensearchpy import OpenSearch, RequestsHttpConnection
        from requests_aws4auth import AWS4Auth

        creds = self._session.get_credentials().get_frozen_credentials()
        auth = AWS4Auth(
            creds.access_key,
            creds.secret_key,
            self._config.region,
            "aoss",
            session_token=creds.token,
        )
        host = endpoint.replace("https://", "").replace("http://", "").rstrip("/")
        body = {
            "settings": {"index.knn": True},
            "mappings": {
                "properties": {
                    "bedrock-knowledge-base-default-vector": {
                        "type": "knn_vector",
                        "dimension": _EMBEDDING_DIM,
                        "method": {
                            "name": "hnsw",
                            "engine": "faiss",
                            "space_type": "l2",
                            "parameters": {"ef_construction": 512, "m": 16},
                        },
                    },
                    "AMAZON_BEDROCK_TEXT_CHUNK": {"type": "text"},
                    "AMAZON_BEDROCK_METADATA": {"type": "text", "index": False},
                }
            },
        }
        # Data-access-policy propagation can take up to 60s after policy
        # creation. Wait, then poll the index creation.
        time.sleep(45)
        last_err: Exception | None = None
        for delay in (0, 15, 20, 30, 30):
            if delay:
                time.sleep(delay)
            client = OpenSearch(
                hosts=[{"host": host, "port": 443}],
                http_auth=auth,
                use_ssl=True,
                verify_certs=True,
                connection_class=RequestsHttpConnection,
                timeout=60,
            )
            try:
                client.indices.create(index=index_name, body=body)
                print(f"  Created vector index: {index_name}")
                return
            except Exception as e:
                last_err = e
                msg = str(e)[:300]
                print(f"  AOSS index create attempt failed: {type(e).__name__}: {msg}")
                if "403" not in msg and "Forbidden" not in msg:
                    raise
        raise RuntimeError(f"vector index create → repeated failure: {last_err}")

    def _create_knowledge_base(
        self, prefix: str, role_arn: str, collection_arn: str, index_name: str
    ) -> str:
        emb_arn = _EMBEDDING_MODEL_ARN_TEMPLATE.format(region=self._config.region)
        result = self._bedrock_agent.create_knowledge_base(
            name=prefix[:50],
            roleArn=role_arn,
            knowledgeBaseConfiguration={
                "type": "VECTOR",
                "vectorKnowledgeBaseConfiguration": {"embeddingModelArn": emb_arn},
            },
            storageConfiguration={
                "type": "OPENSEARCH_SERVERLESS",
                "opensearchServerlessConfiguration": {
                    "collectionArn": collection_arn,
                    "vectorIndexName": index_name,
                    "fieldMapping": {
                        "vectorField": "bedrock-knowledge-base-default-vector",
                        "textField": "AMAZON_BEDROCK_TEXT_CHUNK",
                        "metadataField": "AMAZON_BEDROCK_METADATA",
                    },
                },
            },
        )
        kb_id = result["knowledgeBase"]["knowledgeBaseId"]
        print(f"  Created KB: {kb_id}")
        # Wait for KB to become ACTIVE — StartIngestionJob 4xx's on CREATING.
        self._wait_for_kb_active(kb_id)
        return kb_id

    def _wait_for_kb_active(self, kb_id: str, timeout_s: float = 300.0) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            kb = self._bedrock_agent.get_knowledge_base(knowledgeBaseId=kb_id)["knowledgeBase"]
            status = kb["status"]
            if status == "ACTIVE":
                print(f"  KB ACTIVE: {kb_id}")
                return
            if status in ("FAILED", "DELETING", "DELETE_UNSUCCESSFUL"):
                raise RuntimeError(f"KB {kb_id} entered terminal status {status}")
            time.sleep(5)
        raise TimeoutError(f"KB {kb_id} did not become ACTIVE within {timeout_s}s")

    def _create_data_source(self, kb_id: str, bucket: str) -> str:
        result = self._bedrock_agent.create_data_source(
            knowledgeBaseId=kb_id,
            name=f"s3-{bucket[:50]}",
            dataSourceConfiguration={
                "type": "S3",
                "s3Configuration": {"bucketArn": f"arn:aws:s3:::{bucket}"},
            },
        )
        ds_id = result["dataSource"]["dataSourceId"]
        print(f"  Created data source: {ds_id}")
        return ds_id

    def _start_and_wait_ingestion(self, kb_id: str, ds_id: str) -> None:
        result = self._bedrock_agent.start_ingestion_job(
            knowledgeBaseId=kb_id, dataSourceId=ds_id
        )
        job_id = result["ingestionJob"]["ingestionJobId"]
        print(f"  Started ingestion job: {job_id}")
        deadline = time.monotonic() + 900  # 15 min cap
        while time.monotonic() < deadline:
            status = self._bedrock_agent.get_ingestion_job(
                knowledgeBaseId=kb_id, dataSourceId=ds_id, ingestionJobId=job_id
            )["ingestionJob"]["status"]
            if status == "COMPLETE":
                print("  Ingestion COMPLETE")
                return
            if status in ("FAILED", "STOPPED"):
                raise RuntimeError(f"Ingestion job {job_id} ended with status={status}")
            time.sleep(8)
        raise TimeoutError(f"Ingestion job {job_id} did not COMPLETE within 900s")

    def _empty_and_delete_bucket(self, bucket: str) -> None:
        resp = self._s3.list_objects_v2(Bucket=bucket)
        objects = [{"Key": o["Key"]} for o in resp.get("Contents", [])]
        if objects:
            self._s3.delete_objects(Bucket=bucket, Delete={"Objects": objects})
        self._s3.delete_bucket(Bucket=bucket)


def load_bedrock_adapter_from_env(
    repo_root: Path,
    budget: RunBudget,
    region: str = "us-east-1",
) -> BedrockKBSystem:
    import os
    if not os.environ.get("AWS_ACCESS_KEY_ID"):
        raise RuntimeError("AWS_ACCESS_KEY_ID not set; cannot construct Bedrock adapter.")
    session = boto3.Session(
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name=region,
    )
    return BedrockKBSystem(
        config=BedrockKBConfig(region=region),
        repo_root=repo_root,
        budget=budget,
        session=session,
    )
