from django.db import migrations, models


def set_shahroz_super_admin(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.filter(email__iexact="shahroz@admin.com").update(is_super_admin=True)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="is_super_admin",
            field=models.BooleanField(
                default=False,
                help_text="Can create/delete admins and change user roles.",
            ),
        ),
        migrations.RunPython(set_shahroz_super_admin, migrations.RunPython.noop),
    ]
