"""Microsoft Entra ID (Azure AD) single sign-on via OpenID Connect.

Scoped to a single tenant (not the multi-tenant "common" endpoint), so only
accounts in the UNDP directory can complete the flow at all — there is no
separate domain check to get wrong.
"""
from authlib.integrations.starlette_client import OAuth

from .config import MS_CLIENT_ID, MS_CLIENT_SECRET, MS_TENANT_ID, SSO_ENABLED

oauth = OAuth()

if SSO_ENABLED:
    oauth.register(
        name="microsoft",
        client_id=MS_CLIENT_ID,
        client_secret=MS_CLIENT_SECRET,
        server_metadata_url=(
            "https://login.microsoftonline.com/%s/v2.0/.well-known/openid-configuration"
            % MS_TENANT_ID
        ),
        client_kwargs={"scope": "openid email profile"},
    )
