from __future__ import annotations

import os
import sys
import tempfile
import warnings
from pathlib import Path

from ..config import Config
from ..loader import HTML_EXTS, TEXT_EXTS, load_documents
from ..models import Document
from .base import SourceError, register

# Object keys with these extensions are downloaded and handed to the existing
# file loader/parsers. Derived from the loader's own supported sets so the two
# stay in lockstep: if the loader learns a new format, S3 picks it up for free.
# Everything else (images, archives, other binaries, extension-less keys) is
# skipped without a download.
_SUPPORTED_EXTS = TEXT_EXTS | HTML_EXTS

# ClientError codes that mean the request was rejected for who-you-are reasons
# (bad/absent/expired credentials or denied permissions) rather than something
# wrong with a single object. These are fatal for the whole run — surfacing them
# as a clean credential error beats silently skipping every object.
_AUTH_ERROR_CODES = frozenset(
    {
        "AccessDenied",
        "AccessDeniedException",
        "InvalidAccessKeyId",
        "SignatureDoesNotMatch",
        "InvalidToken",
        "ExpiredToken",
        "ExpiredTokenException",
        "TokenRefreshRequired",
        "AuthorizationHeaderMalformed",
        "UnrecognizedClientException",
        "InvalidClientTokenId",
        "CredentialsNotFound",
    }
)


class S3Error(SourceError):
    """Raised when the S3 source cannot run (missing extra, missing bucket, backend failure)."""


def _import_boto3():
    """Lazily import boto3 (kept out of the module top level so `import corpuslint`
    never requires the optional extra). Returns
    ``(boto3, retryable_errors, NoCredentialsError, ClientError)`` where
    ``retryable_errors`` is the tuple of boto/botocore exception classes to
    translate into a clean :class:`S3Error`."""
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
    except ImportError as e:
        raise S3Error(
            'The S3 source needs the optional extra: pip install "corpuslint[s3]"'
        ) from e
    return boto3, (BotoCoreError, ClientError), NoCredentialsError, ClientError


def _client_error_code(exc) -> str:
    """The AWS error code on a botocore ClientError, or '' if none is present."""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return response.get("Error", {}).get("Code", "") or ""
    return ""


def _make_is_fatal(no_credentials_error, client_error):
    """Predicate: is ``exc`` a credential/auth failure that must abort the run?

    True for a missing-credentials error or a ClientError carrying an auth code
    (see ``_AUTH_ERROR_CODES``). Such an error at object-download time is a
    whole-run problem, not a skip-this-object problem, so it is re-raised for the
    caller to surface as a clean, credential-focused :class:`S3Error`.
    """

    def is_fatal(exc: BaseException) -> bool:
        if isinstance(exc, no_credentials_error):
            return True
        return isinstance(exc, client_error) and _client_error_code(exc) in _AUTH_ERROR_CODES

    return is_fatal


def _list_keys(client, bucket: str, prefix: str) -> list[str]:
    """Every object key under ``prefix`` in ``bucket``.

    Uses the ``list_objects_v2`` paginator, which follows continuation tokens, so
    the whole bucket/prefix is enumerated with no silent cap.
    """
    keys: list[str] = []
    paginator = client.get_paginator("list_objects_v2")
    kwargs: dict = {"Bucket": bucket}
    if prefix:
        kwargs["Prefix"] = prefix
    for page in paginator.paginate(**kwargs):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    return keys


def _document_for_key(client, bucket: str, key: str, config: Config) -> Document | None:
    """Download one object and parse it via the existing file loader.

    Writes the bytes to a temp file carrying the object's extension so the shared
    loader dispatches to the right parser (md/txt → text, html/htm → the HTML
    extractor), then rebrands the resulting document's source to
    ``s3://<bucket>/<key>``. The temp file is always cleaned up.
    """
    ext = Path(key).suffix.lower()
    resp = client.get_object(Bucket=bucket, Key=key)
    body = resp["Body"].read()
    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    try:
        tmp.write(body)
        tmp.close()
        docs = load_documents([tmp.name], config)
    finally:
        if not tmp.closed:
            tmp.close()  # a failed write leaves the handle open; don't leak it
        os.unlink(tmp.name)
    if not docs:
        return None
    return Document(text=docs[0].text, source=f"s3://{bucket}/{key}")


def _documents_from_client(
    client, bucket: str, prefix: str, config: Config, is_fatal=None
) -> list[Document]:
    """Enumerate, filter, download and parse every supported object into a Document.

    Objects whose extension the loader can't parse are skipped before any
    download. A per-object download/parse failure is warned about (by key only —
    never the raw error text, which could carry backend detail) and the run
    continues — *unless* ``is_fatal(exc)`` says it's a credential/auth failure,
    which aborts the run so the caller can surface one clean error instead of a
    warning per object.
    """
    docs: list[Document] = []
    for key in _list_keys(client, bucket, prefix):
        if Path(key).suffix.lower() not in _SUPPORTED_EXTS:
            continue
        try:
            doc = _document_for_key(client, bucket, key, config)
        except Exception as e:  # noqa: BLE001 - one bad object must not abort the run
            if is_fatal is not None and is_fatal(e):
                raise
            warnings.warn(
                f"skipping s3://{bucket}/{key}: {type(e).__name__}",
                UserWarning,
                stacklevel=2,
            )
            continue
        if doc is not None:
            docs.append(doc)
    return docs


def load_s3_documents(
    bucket: str,
    prefix: str,
    config: Config,
    endpoint_url: str | None = None,
    region: str | None = None,
) -> list[Document]:
    """Pull every supported object under ``prefix`` in ``bucket`` into Documents.

    Credentials are resolved by boto3's standard chain (env vars,
    ``~/.aws/credentials``, instance role, …) — never read from source options.
    ``endpoint_url`` targets S3-compatible stores (R2, MinIO, Wasabi, Backblaze).
    """
    boto3, retryable_errors, no_credentials_error, client_error = _import_boto3()
    client_kwargs: dict = {}
    if endpoint_url:
        client_kwargs["endpoint_url"] = endpoint_url
    if region:
        client_kwargs["region_name"] = region
    client = boto3.client("s3", **client_kwargs)
    is_fatal = _make_is_fatal(no_credentials_error, client_error)
    try:
        docs = _documents_from_client(client, bucket, prefix, config, is_fatal=is_fatal)
    except no_credentials_error as e:
        raise S3Error(
            "AWS credentials could not be resolved. Configure them via "
            "AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (and AWS_SESSION_TOKEN) or "
            "~/.aws/credentials before using --source s3."
        ) from e
    except retryable_errors as e:
        # An auth/permission code means the credentials themselves are the
        # problem, so point at the credential chain; otherwise report the failure
        # class only (boto/botocore messages can echo request detail).
        code = _client_error_code(e)
        if code in _AUTH_ERROR_CODES:
            raise S3Error(
                f"AWS denied access to bucket {bucket!r} ({code}). Check the "
                "credentials resolved via AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY "
                "(and AWS_SESSION_TOKEN) or ~/.aws/credentials, and the bucket permissions."
            ) from e
        raise S3Error(
            f"could not access bucket {bucket!r} ({type(e).__name__}); "
            "check the bucket name, region/endpoint_url, and your permissions."
        ) from e
    # Report on stderr (keeps stdout/--json clean) to back the "no silent cap" claim.
    print(f"fetched {len(docs)} documents from s3://{bucket}/{prefix}", file=sys.stderr)
    return docs


class S3Source:
    name = "s3"

    def load(self, config: Config) -> list[Document]:
        # Bucket/prefix/endpoint/region come from source_options; credentials are
        # left entirely to boto3's chain, so no secret ever lands in config.
        opts = config.source_options
        bucket = opts.get("bucket")
        if not bucket:
            raise SourceError(
                "the s3 source requires a bucket "
                "(pass --source-opt bucket=<name>)"
            )
        return load_s3_documents(
            bucket,
            opts.get("prefix", ""),
            config,
            endpoint_url=opts.get("endpoint_url"),
            region=opts.get("region"),
        )


register(S3Source())
