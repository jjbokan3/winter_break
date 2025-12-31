import pulumi
import pulumi_aws as aws

# 1. Create the Bucket (as you had before)
bucket = aws.s3.Bucket("my-bucket")

# 2. Create the IAM User
# This acts as the identity for your Prefect flow
prefect_user = aws.iam.User("prefect-s3-writer")

# 3. Define the Permissions (Policy Document)
# This strictly limits what this user can do.
# We use .apply() because we need the bucket's real ARN, which isn't known until deployment.
def get_policy_json(bucket_arn):
    return aws.iam.get_policy_document(
        statements=[
            aws.iam.GetPolicyDocumentStatementArgs(
                effect="Allow",
                actions=["s3:PutObject"],  # Only allow writing files
                resources=[f"{bucket_arn}/*"],  # Only inside this specific bucket
            )
        ]
    ).json

# We create the policy object using the JSON from above
s3_policy = aws.iam.Policy("prefect-s3-policy",
    policy=bucket.arn.apply(get_policy_json)
)

# 4. Attach the Policy to the User
aws.iam.UserPolicyAttachment("prefect-policy-attachment",
    user=prefect_user.name,
    policy_arn=s3_policy.arn
)

# 5. Generate Access Keys
# These are the actual credentials Prefect will need
access_key = aws.iam.AccessKey("prefect-keys", user=prefect_user.name)

repo = aws.ecr.Repository(
    "prefect-flow-repo",
    force_delete=True,  # optional for dev; be careful in prod
)

# Optional: lifecycle policy to keep latest N images
aws.ecr.LifecyclePolicy(
    "prefect-flow-repo-lifecycle",
    repository=repo.name,
    policy="""{
      "rules": [{
        "rulePriority": 1,
        "description": "Keep last 50 images",
        "selection": {
          "tagStatus": "any",
          "countType": "imageCountMoreThan",
          "countNumber": 50
        },
        "action": { "type": "expire" }
      }]
    }""",
)

pulumi.export("ecr_repo_url", repo.repository_url)
pulumi.export("ecr_repo_name", repo.name)

# 6. Export everything
pulumi.export("bucket_name", bucket.id)
pulumi.export("aws_access_key_id", access_key.id)
pulumi.export("aws_secret_access_key", access_key.secret)
