from news.mongo_db import raw_collection

c = raw_collection()
r1 = c.update_many(
    {"pipeline_status": "failed"},
    {"$set": {"pipeline_status": "pending"}, "$unset": {"pipeline_error": ""}},
)
r2 = c.update_many(
    {"pipeline_status": "processing"},
    {"$set": {"pipeline_status": "pending"}},
)
print({"failed_to_pending": r1.modified_count, "processing_to_pending": r2.modified_count})
