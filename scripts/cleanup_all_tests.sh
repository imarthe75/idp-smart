#!/bin/bash

# IDP Smart - Cleanup Test Data
# Removes everything from DB, Minio and Redis

echo "1/3. Cleaning up Database (Truncating logs, extractions and benchmarks)..."
docker exec idp_db psql -U admin_user -d rpp -c "TRUNCATE idp_smart.document_extractions, idp_smart.process_logs, idp_smart.hardware_benchmarks CASCADE;"

echo "2/3. Cleaning up MinIO (Deleting all files in idp-documents)..."
docker run --rm --network idp-smart_default --entrypoint /bin/sh minio/mc -c "\
  mc alias set myminio http://minio:9000 admin minio_password123; \
  mc rm --recursive --force myminio/idp-documents/; \
  mc mb myminio/idp-documents; \
  mc event add myminio/idp-documents arn:minio:sqs::primary:webhook --event put; \
"

echo "3/3. Cleaning up Valkey (Redis) Cache and Queues..."
docker exec idp_valkey valkey-cli flushall

echo "Cleanup finished! System is now clean of previous tests."
