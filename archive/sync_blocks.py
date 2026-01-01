import os
import sys
from prefect_aws import AwsCredentials, S3Bucket

# We expect these to be set by the GitHub Action workflow
ACCESS_KEY = os.environ.get("PULUMI_OUTPUT_ACCESS_KEY")
SECRET_KEY = os.environ.get("PULUMI_OUTPUT_SECRET_KEY")
BUCKET_NAME = os.environ.get("PULUMI_OUTPUT_BUCKET_NAME")

if not all([ACCESS_KEY, SECRET_KEY, BUCKET_NAME]):
    print("❌ Missing environment variables. Skipping block update.")
    sys.exit(1)


def main():
    print("🔄 Syncing Pulumi outputs to Prefect Blocks...")

    # 1. Update Credentials Block
    creds_block = AwsCredentials(
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
    )
    creds_block.save(name="prefect-s3-writer-creds", overwrite=True)
    print("✅ AwsCredentials block updated.")

    # 2. Update Bucket Block
    bucket_block = S3Bucket(
        bucket_name=BUCKET_NAME,
        credentials=creds_block,
    )
    bucket_block.save(name="my-data-bucket", overwrite=True)
    print("✅ S3Bucket block updated.")


if __name__ == "__main__":
    main()