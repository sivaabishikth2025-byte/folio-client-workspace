"""Private direct uploads and speech services using short-lived deployment identity."""
import os
import time
import threading
from contextvars import ContextVar
import boto3
from botocore import UNSIGNED
from botocore.config import Config

identity = ContextVar('aws_identity', default=None)
_lock = threading.Lock()
_clients = {}
_credentials = None
MAX_FILE = 50 * 1024 * 1024

def bucket():
    return os.getenv('FOLIO_S3_BUCKET', '')

def client(service):
    global _credentials
    role = os.getenv('FOLIO_AWS_ROLE_ARN', '')
    region = os.getenv('BEDROCK_REGION', 'us-east-1')
    with _lock:
        if role and (not _credentials or _credentials[0] < time.time()):
            sts = boto3.client('sts', region_name=region, config=Config(signature_version=UNSIGNED))
            c = sts.assume_role_with_web_identity(RoleArn=role, RoleSessionName='folio-files-voice',
                WebIdentityToken=identity.get() or os.environ['VERCEL_OIDC_TOKEN'], DurationSeconds=900)['Credentials']
            _credentials = (time.time()+600, dict(aws_access_key_id=c['AccessKeyId'], aws_secret_access_key=c['SecretAccessKey'], aws_session_token=c['SessionToken']))
            _clients.clear()
        key = (service, role, region)
        if key not in _clients:
            _clients[key] = boto3.client(service, region_name=region,
                config=Config(signature_version='s3v4' if service=='s3' else 'v4', s3={'addressing_style':'virtual','us_east_1_regional_endpoint':'regional'}, connect_timeout=10, read_timeout=20 if service=='textract' else 90, retries={'total_max_attempts':2 if service=='textract' else 3,'mode':'standard'}),
                **(_credentials[1] if role else {}))
        return _clients[key]

def read(key, version=None):
    kwargs = {'VersionId':version} if version else {}
    with client('s3').get_object(Bucket=bucket(), Key=key, **kwargs)['Body'] as body:
        raw = body.read(MAX_FILE+1)
    if len(raw)>MAX_FILE:raise ValueError('File exceeds 50 MB.')
    return raw

def download_url(key, name):
    from urllib.parse import quote
    return client('s3').generate_presigned_url('get_object',Params={'Bucket':bucket(),'Key':key,
        'ResponseContentDisposition':"attachment; filename*=UTF-8''"+quote(name)},ExpiresIn=60)
