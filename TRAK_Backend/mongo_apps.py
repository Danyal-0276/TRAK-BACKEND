"""AppConfig overrides for Django's contrib apps so they use ObjectIdAutoField.

django-mongodb-backend cannot use Django's default BigAutoField. Each contrib
app whose models declare an AutoField must opt in to ObjectIdAutoField instead.
See https://django-mongodb-backend.readthedocs.io/en/latest/howto/contrib-apps/.
"""

from django.contrib.admin.apps import AdminConfig
from django.contrib.auth.apps import AuthConfig
from django.contrib.contenttypes.apps import ContentTypesConfig

OBJECTID_AUTO_FIELD = "django_mongodb_backend.fields.ObjectIdAutoField"


class MongoAdminConfig(AdminConfig):
    default_auto_field = OBJECTID_AUTO_FIELD


class MongoAuthConfig(AuthConfig):
    default_auto_field = OBJECTID_AUTO_FIELD


class MongoContentTypesConfig(ContentTypesConfig):
    default_auto_field = OBJECTID_AUTO_FIELD
