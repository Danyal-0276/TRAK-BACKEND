from django.core.management.base import BaseCommand

from news import platform_taxonomy


class Command(BaseCommand):
    help = "Seed MongoDB admin_settings with default platform categories and subcategories."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sync-connections",
            action="store_true",
            help="Add scrape sources from sources_catalog.py that are not yet in connections.",
        )

    def handle(self, *args, **options):
        seeded = platform_taxonomy.seed_taxonomy_if_empty()
        conn_seeded = platform_taxonomy.seed_connections_if_empty()
        if seeded:
            self.stdout.write(self.style.SUCCESS("Seeded default platform taxonomy."))
        else:
            cats = platform_taxonomy.list_categories(seed=False)
            self.stdout.write(f"Taxonomy already present ({len(cats)} categories).")
        if conn_seeded:
            self.stdout.write(self.style.SUCCESS("Seeded default scrape connections."))
        if options.get("sync_connections"):
            added = platform_taxonomy.merge_catalog_connections()
            self.stdout.write(self.style.SUCCESS(f"Synced {added} connection(s) from catalog."))
        renamed = platform_taxonomy.refresh_connection_labels_from_catalog()
        if renamed:
            self.stdout.write(self.style.SUCCESS(f"Refreshed {renamed} connection label(s)."))
        conns = platform_taxonomy.list_connections()
        self.stdout.write(f"Connections: {len(conns)} total.")
