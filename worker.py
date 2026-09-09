import os

from workers import env

# Make Cloudflare Worker secrets/environment variables available
# to the existing Flask configuration through os.environ.
_CONFIG_KEYS = (
    "SECRET_KEY",
    "ADMIN_USERNAME",
    "ADMIN_PASSWORD",
    "ITEMS_PER_PAGE",
    "CLOUDINARY_CLOUD_NAME",
    "CLOUDINARY_API_KEY",
    "CLOUDINARY_API_SECRET",
)

for key in _CONFIG_KEYS:
    try:
        value = getattr(env, key, None)
        if value is not None:
            os.environ[key] = str(value)
    except Exception:
        pass


from werkzeug.middleware.dispatcher import DispatcherMiddleware
from workers import wsgi

from public_site.app import create_app as create_public_app
from admin_site.app import create_app as create_admin_app


public_app = create_public_app()
admin_app = create_admin_app()

app = DispatcherMiddleware(
    public_app,
    {
        "/admin": admin_app,
    },
)

Default = wsgi.entrypoint(app)
