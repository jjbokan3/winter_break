from prefect import flow, task, get_run_logger
from prefect_aws import S3Bucket
import pandas as pd


@task
def upload_data():
    logger = get_run_logger()

    # We just LOAD the block. We assume GitHub Actions has already created/updated it.
    try:
        s3_block = S3Bucket.load("my-data-bucket")
        logger.info(f"✅ Loaded S3 Block pointing to: {s3_block.bucket_name}")

        # Example usage:
        s3_block.write_path("hello_from_prefect.txt", b"Hello World!")
        logger.info("Uploaded file to S3!")

    except ValueError:
        logger.error("❌ S3 Block not found! Did the GitHub Action run successfully?")
        raise


@flow(name="s3-uploader-flow")
def my_data_flow():
    upload_data()


if __name__ == "__main__":
    my_data_flow()