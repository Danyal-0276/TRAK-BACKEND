#!/bin/sh
set -e

# Wait for MongoDB only when using the local-db compose override (host "mongodb").
case "${MONGODB_URI:-}" in
  *mongodb://mongodb*|*@mongodb:*)
    echo "Waiting for local MongoDB..."
    python -c "
import os, sys, time
from pymongo import MongoClient
uri = os.environ.get('MONGODB_URI', 'mongodb://mongodb:27017')
for _ in range(30):
    try:
        MongoClient(uri, serverSelectionTimeoutMS=2000).admin.command('ping')
        print('MongoDB is ready.')
        sys.exit(0)
    except Exception:
        time.sleep(2)
sys.exit(1)
"
    ;;
esac

echo "Running migrations..."
python manage.py migrate --noinput

echo "Collecting static files..."
python manage.py collectstatic --noinput

echo "Starting Daphne..."
exec daphne -b 0.0.0.0 -p 8000 TRAK_Backend.asgi:application
