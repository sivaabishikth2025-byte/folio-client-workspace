"""Individual Cognito accounts. AWS validates tokens, including revocation, on access."""
import os
import hashlib
import time
import re
from functools import lru_cache
import boto3
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError
from fastapi import HTTPException

def client_id():return os.getenv('COGNITO_CLIENT_ID','').strip()

def enabled():return bool(client_id())

@lru_cache(maxsize=1)
def cognito():
    return boto3.client('cognito-idp',region_name=os.getenv('BEDROCK_REGION','us-east-1'),
        config=Config(signature_version=UNSIGNED,connect_timeout=10,read_timeout=20,retries={'total_max_attempts':2,'mode':'standard'}))

def authenticate(request,store,cookie):
    token=request.cookies.get(cookie,'')
    if not token:return False
    key=hashlib.sha256(token.encode()).hexdigest()
    if store.auth_state()['sessions'].get(key,0)<time.time():return False
    try:
        user=cognito().get_user(AccessToken=token)
        request.state.workspace_id=identity_scope(user)
        request.state.username=user['Username']
        return True
    except (cognito().exceptions.NotAuthorizedException,cognito().exceptions.InvalidParameterException,HTTPException):return False


def identity_scope(user):
    attributes={a['Name']:a['Value'] for a in user.get('UserAttributes',[])}
    sub=attributes.get('sub','')
    if not sub:raise HTTPException(401,'This account has no verified identity.')
    shared={s.strip() for s in os.getenv('FOLIO_SHARED_ACCOUNT_SUBS','').split(',') if s.strip()}
    # Existing assessment members retain their shared workspace by immutable Cognito sub.
    # All self-service accounts get private state, including copies of the fictional seeds.
    if sub in shared:return ''
    if attributes.get('email_verified')!='true':
        raise HTTPException(403,{'code':'EMAIL_NOT_VERIFIED','message':'Verify your email to finish creating your account.'})
    return hashlib.sha256((client_id()+':'+sub).encode()).hexdigest()


def reserve_attempt(store,request,action,username='',email=False):
    if not enabled():raise HTTPException(503,'Account services are temporarily unavailable.')
    peer=request.headers.get('x-vercel-forwarded-for','').split(',')[0] if os.getenv('VERCEL') else (request.client.host if request.client else 'unknown')
    keys=[hashlib.sha256((action+':ip:'+peer).encode()).hexdigest()]
    if username:keys.append(hashlib.sha256((action+':user:'+username.strip().casefold()).encode()).hexdigest())
    def reserve(s):
        current=time.time()
        s['attempts']={k:[t for t in ts if t>current-900] for k,ts in s['attempts'].items() if any(t>current-900 for t in ts)}
        if sum(len(v) for v in s['attempts'].values())>=200:return False
        if any(len(s['attempts'].get(k,[]))>=(5 if email else 10) for k in keys):return False
        if email:
            # Share the cooldown across signup, resend and recovery for the same recipient.
            mail_key=hashlib.sha256(('email:'+username.strip().casefold()).encode()).hexdigest()
            recent=s.setdefault('email_attempts',{})
            recent={k:t for k,t in recent.items() if t>current-60}
            s['email_attempts']=recent
            if mail_key in recent:return False
            recent[mail_key]=current
        for key in keys:s['attempts'][key]=s['attempts'].get(key,[])+[current]
        return True
    if not store.auth_state(reserve):raise HTTPException(429,'Too many attempts. Wait a minute before requesting another code, or try again in 15 minutes.')


def account_error(exc):
    """Sanitize AWS errors at the public account API boundary; never return raw payloads."""
    code=exc.response['Error']['Code']
    if code in ('InvalidPasswordException','PasswordHistoryPolicyViolationException'):
        raise HTTPException(400,'Use a new password with at least 12 characters, uppercase and lowercase letters, a number and a symbol.')
    if code in ('CodeMismatchException','ExpiredCodeException'):
        raise HTTPException(400,'That code is incorrect or expired. Check your email or request a new code.')
    if code in ('LimitExceededException','TooManyRequestsException','TooManyFailedAttemptsException'):
        raise HTTPException(429,'AWS has temporarily limited account requests. Please try again later.')
    if code in ('CodeDeliveryFailureException','InvalidEmailRoleAccessPolicyException'):
        raise HTTPException(503,'Email delivery is temporarily unavailable. Please try again later.')
    raise HTTPException(400,'Could not complete this request. Check your details or return to sign in.')


def sign_up(store,request,body):
    email=body.email.strip().casefold()
    name=' '.join(body.name.split())
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email) or not name or any(ord(c)<32 for c in name):
        raise HTTPException(400,'Enter your name and a valid email address.')
    reserve_attempt(store,request,'signup',email,email=True)
    try:
        result=cognito().sign_up(ClientId=client_id(),Username=email,Password=body.password,
            UserAttributes=[{'Name':'email','Value':email},{'Name':'name','Value':name}])
    except ClientError as exc:
        if exc.response['Error']['Code']=='UsernameExistsException':
            # Same response for existing accounts; no email/account enumeration.
            return {'confirmation_required':True,'username':email}
        account_error(exc)
    return {'confirmation_required':not result.get('UserConfirmed',False),'username':email}


def confirm_signup(store,request,body):
    username=body.username.strip().casefold()
    reserve_attempt(store,request,'confirm',username)
    try:cognito().confirm_sign_up(ClientId=client_id(),Username=username,ConfirmationCode=body.code,ForceAliasCreation=False)
    except ClientError as exc:account_error(exc)
    return {'confirmed':True}


def resend_code(store,request,body):
    username=body.username.strip().casefold()
    reserve_attempt(store,request,'resend',username,email=True)
    try:cognito().resend_confirmation_code(ClientId=client_id(),Username=username)
    except ClientError as exc:
        if exc.response['Error']['Code'] not in ('UserNotFoundException','InvalidParameterException','NotAuthorizedException'):account_error(exc)
    return {'sent':True,'message':'If this account is awaiting verification, a new code has been sent.'}


def forgot_password(store,request,body):
    username=body.username.strip().casefold()
    reserve_attempt(store,request,'forgot',username,email=True)
    try:cognito().forgot_password(ClientId=client_id(),Username=username)
    except ClientError as exc:
        if exc.response['Error']['Code'] not in ('UserNotFoundException','InvalidParameterException','NotAuthorizedException'):account_error(exc)
    return {'sent':True,'message':'If the account has a verified email, a password reset code has been sent.'}


def reset_password(store,request,body):
    username=body.username.strip().casefold()
    reserve_attempt(store,request,'reset',username)
    try:cognito().confirm_forgot_password(ClientId=client_id(),Username=username,ConfirmationCode=body.code,Password=body.password)
    except ClientError as exc:account_error(exc)
    return {'reset':True}

def sign_in(store,request,body):
    reserve_attempt(store,request,'login',body.username)
    try:
        if body.challenge_session:
            result=cognito().respond_to_auth_challenge(ClientId=client_id(),ChallengeName='NEW_PASSWORD_REQUIRED',Session=body.challenge_session,
                ChallengeResponses={'USERNAME':body.username,'NEW_PASSWORD':body.new_password})
        else:result=cognito().initiate_auth(ClientId=client_id(),AuthFlow='USER_PASSWORD_AUTH',AuthParameters={'USERNAME':body.username,'PASSWORD':body.password})
    except ClientError as exc:
        if exc.response['Error']['Code']=='UserNotConfirmedException':
            raise HTTPException(403,{'code':'EMAIL_NOT_VERIFIED','message':'Verify your email to finish creating your account.'})
        if exc.response['Error']['Code']=='InvalidPasswordException':raise HTTPException(400,'Use at least 12 characters with upper/lowercase letters, a number and a symbol.')
        raise HTTPException(401,'Could not sign in. Check your email and password, or use Forgot password.')
    if result.get('ChallengeName')=='NEW_PASSWORD_REQUIRED':return {'challenge':'NEW_PASSWORD_REQUIRED','session':result['Session']}
    if 'AuthenticationResult' not in result:raise HTTPException(401,'This account requires administrator assistance to complete sign-in.')
    token=result['AuthenticationResult']['AccessToken'];expires=min(result['AuthenticationResult']['ExpiresIn'],3600)
    identity_scope(cognito().get_user(AccessToken=token))
    def save(s):
        s['sessions']={k:v for k,v in s['sessions'].items() if v>time.time()}
        s['sessions'][hashlib.sha256(token.encode()).hexdigest()]=time.time()+expires
    store.auth_state(save)
    return {'token':token,'expires':expires}

def sign_out(store,request,cookie):
    token=request.cookies.get(cookie,'')
    store.auth_state(lambda s:s['sessions'].pop(hashlib.sha256(token.encode()).hexdigest(),None))
    try:cognito().global_sign_out(AccessToken=token)
    except cognito().exceptions.NotAuthorizedException:pass

def change_password(request,previous,proposed,cookie):
    try:cognito().change_password(AccessToken=request.cookies[cookie],PreviousPassword=previous,ProposedPassword=proposed)
    except ClientError:raise HTTPException(400,'Password change failed. Check the current password and use 12+ characters with upper/lowercase letters, a number and a symbol.')
