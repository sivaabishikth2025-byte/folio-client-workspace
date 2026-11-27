"""Server-side provider configuration. Credentials never enter workspace state.

If ANTHROPIC_API_KEY is set, Claude is called through the Anthropic API.
Otherwise Claude runs on AWS Bedrock (the hosted demo uses Bedrock).
"""
import os
import threading
import time

import boto3
from botocore import UNSIGNED
from botocore.config import Config

DEFAULT_BEDROCK_MODEL = 'us.anthropic.claude-sonnet-4-6'
DEFAULT_ANTHROPIC_MODEL = 'claude-sonnet-5-5'
_lock = threading.Lock()
_cached = None


def anthropic_configured():
    return bool(os.getenv('ANTHROPIC_API_KEY', '').strip())


def bedrock_configured():
    return bool(os.getenv('AWS_BEARER_TOKEN_BEDROCK', '').strip() or
                os.getenv('FOLIO_AWS_ROLE_ARN', '').strip() or
                os.getenv('AWS_BEDROCK_ENABLED') == '1')


def active_provider():
    """Anthropic when its key is set, otherwise Bedrock."""
    return 'anthropic' if anthropic_configured() else 'bedrock'


def catalog():
    """The active provider is always first."""
    anthropic = {'id': 'anthropic', 'name': 'Anthropic', 'detail': 'Claude API',
                 'model': os.getenv('ANTHROPIC_MODEL', '').strip() or DEFAULT_ANTHROPIC_MODEL,
                 'configured': anthropic_configured(), 'auth': 'Server API key'}
    bedrock = {'id': 'bedrock', 'name': 'AWS Bedrock', 'detail': 'Claude on AWS',
               'model': os.getenv('BEDROCK_MODEL_ID', DEFAULT_BEDROCK_MODEL),
               'configured': bedrock_configured(),
               'auth': 'AWS role' if os.getenv('FOLIO_AWS_ROLE_ARN') else 'Server credentials'}
    return [anthropic, bedrock] if active_provider() == 'anthropic' else [bedrock]


def bedrock_client(oidc_token=None):
    """Exchange only the deployment identity for a narrowly scoped AWS role."""
    global _cached
    region = os.getenv('BEDROCK_REGION', 'us-east-1')
    role = os.getenv('FOLIO_AWS_ROLE_ARN', '')
    config = Config(connect_timeout=10, read_timeout=120,
                    retries={'total_max_attempts': 3, 'mode': 'adaptive'})
    if not role:
        # SDK supports AWS_BEARER_TOKEN_BEDROCK or its standard credential chain.
        return boto3.client('bedrock-runtime', region_name=region, config=config)
    with _lock:
        if _cached and _cached[0] == role and _cached[1] > time.time():
            return _cached[2]
        token = oidc_token or os.getenv('VERCEL_OIDC_TOKEN')
        if not token:
            raise ValueError('AWS needs a deployment identity. Configure Vercel OIDC, or use local AWS credentials without FOLIO_AWS_ROLE_ARN.')
        sts = boto3.client('sts', region_name=region,
                          config=Config(signature_version=UNSIGNED, connect_timeout=10, read_timeout=20,
                                        retries={'total_max_attempts': 2, 'mode': 'standard'}))
        credentials = sts.assume_role_with_web_identity(
            RoleArn=role, RoleSessionName='folio-chat', WebIdentityToken=token,
            DurationSeconds=900)['Credentials']
        client = boto3.client('bedrock-runtime', region_name=region, config=config,
                              aws_access_key_id=credentials['AccessKeyId'],
                              aws_secret_access_key=credentials['SecretAccessKey'],
                              aws_session_token=credentials['SessionToken'])
        _cached = (role, time.time() + 600, client)
        return client
