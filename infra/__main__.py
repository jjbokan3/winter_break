import pulumi
import pulumi_aws as aws

config = pulumi.Config()

# --- Required / recommended config values ---
# sshCidr: your public IP in CIDR form, e.g. "203.0.113.10/32"
ssh_cidr = config.get("sshCidr") or "72.76.166.39/32"  # <-- change this; 0.0.0.0/0 is NOT recommended
key_name = config.get("keyName")                  # optional: EC2 KeyPair name if you want SSH
subnet_id = config.get("subnetId")                # required
vpc_id = config.get("vpcId")                      # required

# Optional: if you want the EC2 to have a public IP (useful for SSH)
associate_public_ip = config.get_bool("associatePublicIp") or False

# --- Create subnet if subnetId is not provided ---
subnet_id = config.get("subnetId")

if not subnet_id:
    # Get available AZs
    azs = aws.get_availability_zones(state="available")

    # Create a subnet in the first available AZ
    subnet = aws.ec2.Subnet(
        "clickhouse-subnet",
        vpc_id=vpc_id,
        cidr_block="10.0.0.0/24",  # Adjust this based on your VPC CIDR
        availability_zone=azs.names[0],
        map_public_ip_on_launch=associate_public_ip,
        tags={"Name": "clickhouse-subnet"},
    )
    subnet_id = subnet.id

    # If you want public access, you'll also need an Internet Gateway and route table
    if associate_public_ip:
        igw = aws.ec2.InternetGateway(
            "clickhouse-igw",
            vpc_id=vpc_id,
            tags={"Name": "clickhouse-igw"},
        )

        route_table = aws.ec2.RouteTable(
            "clickhouse-rt",
            vpc_id=vpc_id,
            routes=[
                aws.ec2.RouteTableRouteArgs(
                    cidr_block="0.0.0.0/0",
                    gateway_id=igw.id,
                )
            ],
            tags={"Name": "clickhouse-rt"},
        )

        route_table_association = aws.ec2.RouteTableAssociation(
            "clickhouse-rta",
            subnet_id=subnet_id,
            route_table_id=route_table.id,
        )


# --- Security group for ECS tasks (you'll attach this to ECS task ENIs later) ---
sg_ecs_tasks = aws.ec2.SecurityGroup(
    "ecs-tasks-sg",
    description="Security group for ECS tasks (Prefect workers, API, etc.)",
    vpc_id=vpc_id,
    # Allow all outbound
    egress=[
        aws.ec2.SecurityGroupEgressArgs(
            protocol="-1",
            from_port=0,
            to_port=0,
            cidr_blocks=["0.0.0.0/0"],
        )
    ],
    tags={"Name": "sg-ecs-tasks"},
)

# Allow ECS tasks to communicate with each other (self-referencing rule)
sg_ecs_tasks_ingress = aws.ec2.SecurityGroupRule(
    "ecs-tasks-self-ingress",
    type="ingress",
    from_port=0,
    to_port=65535,
    protocol="tcp",
    source_security_group_id=sg_ecs_tasks.id,
    security_group_id=sg_ecs_tasks.id,
    description="Allow ECS tasks to communicate with each other",
)


# --- Security group for ClickHouse EC2 ---
# Inbound: ONLY allow ClickHouse ports from the ECS tasks SG
ingress_rules = [
    aws.ec2.SecurityGroupIngressArgs(
        protocol="tcp",
        from_port=8123,
        to_port=8123,
        security_groups=[sg_ecs_tasks.id],
        description="ClickHouse HTTP interface from ECS tasks",
    ),
    aws.ec2.SecurityGroupIngressArgs(
        protocol="tcp",
        from_port=9000,
        to_port=9000,
        security_groups=[sg_ecs_tasks.id],
        description="ClickHouse native interface from ECS tasks (optional)",
    ),
]

# Optional SSH access
if ssh_cidr and ssh_cidr != "0.0.0.0/0":
    ingress_rules.append(
        aws.ec2.SecurityGroupIngressArgs(
            protocol="tcp",
            from_port=22,
            to_port=22,
            cidr_blocks=[ssh_cidr],
            description="SSH from your IP only",
        )
    )
    ingress_rules.append(
        aws.ec2.SecurityGroupIngressArgs(
            protocol="tcp",
            from_port=8123,
            to_port=8123,
            cidr_blocks=[ssh_cidr],
            description="ClickHouse HTTP from your IP only",
        )
    )

sg_clickhouse = aws.ec2.SecurityGroup(
    "clickhouse-sg",
    description="Security group for ClickHouse EC2 (private; ECS-only access)",
    vpc_id=vpc_id,
    ingress=ingress_rules,
    egress=[
        aws.ec2.SecurityGroupEgressArgs(
            protocol="-1",
            from_port=0,
            to_port=0,
            cidr_blocks=["0.0.0.0/0"],
        )
    ],
    tags={"Name": "sg-clickhouse"},
)

# --- AMI (Ubuntu 22.04 LTS) ---
ami = aws.ec2.get_ami(
    most_recent=True,
    owners=["099720109477"],  # Canonical
    filters=[
        aws.ec2.GetAmiFilterArgs(name="name", values=["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]),
        aws.ec2.GetAmiFilterArgs(name="virtualization-type", values=["hvm"]),
    ],
)

# --- User data: mount the attached EBS volume at /var/lib/clickhouse ---
# Note: On Nitro instances, the attached device name often shows up as /dev/nvme1n1, etc.
# This script finds the non-root disk, formats it if needed, mounts it, and persists in /etc/fstab.
user_data = """#!/bin/bash
set -euo pipefail

apt-get update -y
apt-get install -y ca-certificates curl gnupg lsb-release xfsprogs

# Install Docker (for convenience; you can run ClickHouse via packages later if you prefer)
apt-get install -y docker.io
systemctl enable docker
systemctl start docker

# Find root disk and data disk
ROOT_SRC=$(findmnt -n -o SOURCE /)
ROOT_DISK="/dev/$(lsblk -no PKNAME "$ROOT_SRC" 2>/dev/null || true)"

DATA_DISK=$(lsblk -ndo NAME,TYPE | awk '$2=="disk"{print "/dev/"$1}' | grep -v "$ROOT_DISK" | head -n1 || true)
if [ -z "${DATA_DISK}" ]; then
  echo "No data disk found; exiting." >&2
  exit 1
fi

# Format if empty
if ! blkid "${DATA_DISK}" >/dev/null 2>&1; then
  mkfs.ext4 -F "${DATA_DISK}"
fi

mkdir -p /var/lib/clickhouse
UUID=$(blkid -s UUID -o value "${DATA_DISK}")
grep -q "${UUID}" /etc/fstab || echo "UUID=${UUID} /var/lib/clickhouse ext4 defaults,nofail 0 2" >> /etc/fstab

mount -a

echo "Mounted ${DATA_DISK} at /var/lib/clickhouse"

# Add this to start ClickHouse
    docker run -d \
      --name clickhouse-server \
      --restart always \
      -p 8123:8123 \
      -p 9000:9000 \
      --ulimit nofile=262144:262144 \
      -v /var/lib/clickhouse:/var/lib/clickhouse \
      clickhouse/clickhouse-server
"""

# --- EC2 instance ---
clickhouse_instance = aws.ec2.Instance(
    "clickhouse-ec2",
    ami=ami.id,
    instance_type="t3.small",
    subnet_id=subnet_id,
    vpc_security_group_ids=[sg_clickhouse.id],
    associate_public_ip_address=associate_public_ip,
    key_name=key_name,
    user_data=user_data,
    tags={"Name": "clickhouse-ec2"},
)

# Add after clickhouse_instance creation
spot_termination_alarm = aws.cloudwatch.MetricAlarm(
    "clickhouse-spot-termination",
    comparison_operator="GreaterThanThreshold",
    evaluation_periods=1,
    metric_name="StatusCheckFailed",
    namespace="AWS/EC2",
    period=60,
    statistic="Maximum",
    threshold=0,
    alarm_description="Alert if ClickHouse spot instance fails",
    dimensions={"InstanceId": clickhouse_instance.id},
)


# --- EBS data volume (persistent ClickHouse data) ---
# Size is configurable; default 75GB
data_volume_size_gb = config.get_int("clickhouseDataVolumeGb") or 75

clickhouse_data_volume = aws.ebs.Volume(
    "clickhouse-data",
    availability_zone=clickhouse_instance.availability_zone,
    size=data_volume_size_gb,
    type="gp3",
    tags={"Name": "clickhouse-data"},
)

# Attach volume (device name is a hint; Nitro remaps it internally)
clickhouse_data_attachment = aws.ec2.VolumeAttachment(
    "clickhouse-data-attach",
    device_name="/dev/sdf",
    volume_id=clickhouse_data_volume.id,
    instance_id=clickhouse_instance.id,
)


# --- Outputs ---
pulumi.export("sg_ecs_tasks_id", sg_ecs_tasks.id)
pulumi.export("sg_clickhouse_id", sg_clickhouse.id)
pulumi.export("clickhouse_instance_id", clickhouse_instance.id)
pulumi.export("clickhouse_private_ip", clickhouse_instance.private_ip)
pulumi.export("clickhouse_public_ip", clickhouse_instance.public_ip)

# --- ECS Cluster for Prefect ---
ecs_cluster = aws.ecs.Cluster(
    "prefect-cluster",
    name="prefect-cluster",
    tags={"Name": "prefect-cluster"},
)

# --- CloudWatch Log Group for Prefect logs ---
prefect_log_group = aws.cloudwatch.LogGroup(
    "prefect-logs",
    name="/ecs/prefect-server",
    retention_in_days=7,
)

# --- IAM Role for ECS Task Execution (required for Fargate) ---
task_execution_role = aws.iam.Role(
    "prefect-task-execution-role",
    assume_role_policy="""{
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "ecs-tasks.amazonaws.com"},
            "Action": "sts:AssumeRole"
        }]
    }""",
)

aws.iam.RolePolicyAttachment(
    "prefect-task-execution-policy",
    role=task_execution_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
)

# --- IAM Role for the Task itself (optional, for AWS SDK access from container) ---
task_role = aws.iam.Role(
    "prefect-task-role",
    assume_role_policy="""{
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "ecs-tasks.amazonaws.com"},
            "Action": "sts:AssumeRole"
        }]
    }""",
)

# --- Prefect Server Task Definition ---
prefect_task_definition = aws.ecs.TaskDefinition(
    "prefect-server-task",
    family="prefect-server",
    network_mode="awsvpc",
    requires_compatibilities=["FARGATE"],
    cpu="512",  # 0.5 vCPU
    memory="1024",  # 1 GB
    execution_role_arn=task_execution_role.arn,
    task_role_arn=task_role.arn,
    container_definitions=pulumi.Output.all(prefect_log_group.name, clickhouse_instance.private_ip).apply(
        lambda args: f"""[
            {{
                "name": "prefect-server",
                "image": "prefecthq/prefect:2-latest",
                "essential": true,
                "command": ["prefect", "server", "start", "--host", "0.0.0.0"],
                "portMappings": [
                    {{
                        "containerPort": 4200,
                        "protocol": "tcp"
                    }}
                ],
                "environment": [
                    {{
                        "name": "PREFECT_SERVER_API_HOST",
                        "value": "0.0.0.0"
                    }},
                    {{
                        "name": "PREFECT_API_DATABASE_CONNECTION_URL",
                        "value": "sqlite+aiosqlite:////root/.prefect/prefect.db"
                    }}
                ],
                "logConfiguration": {{
                    "logDriver": "awslogs",
                    "options": {{
                        "awslogs-group": "{args[0]}",
                        "awslogs-region": "{aws.config.region}",
                        "awslogs-stream-prefix": "prefect"
                    }}
                }}
            }}
        ]"""
    ),
)

# --- Update ECS Security Group to allow access to Prefect UI ---
sg_prefect_ingress = aws.ec2.SecurityGroupRule(
    "prefect-ui-access-sg",
    type="ingress",
    from_port=4200,
    to_port=4200,
    protocol="tcp",
    cidr_blocks=[ssh_cidr] if ssh_cidr and ssh_cidr != "0.0.0.0/0" else ["0.0.0.0/0"],
    security_group_id=sg_ecs_tasks.id,
    description="Prefect UI access",
)

# --- Service Discovery Setup ---
# Create a private DNS namespace for internal service communication
prefect_namespace = aws.servicediscovery.PrivateDnsNamespace(
    "prefect-namespace",
    name="prefect.local",
    vpc=vpc_id,
    description="Private DNS namespace for Prefect services",
)

# Register Prefect server for service discovery
prefect_service_discovery = aws.servicediscovery.Service(
    "prefect-server-discovery",
    name="prefect-server",
    dns_config=aws.servicediscovery.ServiceDnsConfigArgs(
        namespace_id=prefect_namespace.id,
        dns_records=[
            aws.servicediscovery.ServiceDnsConfigDnsRecordArgs(
                ttl=10,
                type="A",
            )
        ],
        routing_policy="MULTIVALUE",
    ),
    health_check_custom_config=aws.servicediscovery.ServiceHealthCheckCustomConfigArgs(
        failure_threshold=1,
    ),
)

# Update Prefect server service to register with Service Discovery
prefect_service = aws.ecs.Service(
    "prefect-server-service",
    cluster=ecs_cluster.arn,
    task_definition=prefect_task_definition.arn,
    desired_count=1,
    launch_type="FARGATE",
    enable_execute_command=True,
    network_configuration=aws.ecs.ServiceNetworkConfigurationArgs(
        assign_public_ip=associate_public_ip,
        subnets=[subnet_id],
        security_groups=[sg_ecs_tasks.id],
    ),
    service_registries=aws.ecs.ServiceServiceRegistriesArgs(
        registry_arn=prefect_service_discovery.arn,
        container_name="prefect-server",  # Add this - must match container name in task definition
    ),
    # This is critical - ensures proper ordering
    opts=pulumi.ResourceOptions(depends_on=[prefect_service_discovery]),
)

# --- Prefect Worker Task Definition ---
prefect_worker_task = aws.ecs.TaskDefinition(
    "prefect-worker-task",
    family="prefect-worker",
    network_mode="awsvpc",
    requires_compatibilities=["FARGATE"],
    cpu="256",
    memory="512",
    execution_role_arn=task_execution_role.arn,
    task_role_arn=task_role.arn,
    container_definitions=pulumi.Output.all(
        prefect_log_group.name,
        clickhouse_instance.private_ip
    ).apply(
        lambda args: f"""[
            {{
                "name": "prefect-worker",
                "image": "prefecthq/prefect:2-latest",
                "essential": true,
                "command": [
                    "/bin/bash",
                    "-c",
                    "echo '=== INSTALLING DIAGNOSTIC TOOLS ===' && apt-get update -qq && apt-get install -y -qq curl dnsutils iputils-ping netcat-traditional && echo '=== WORKER STARTING ===' && echo 'PREFECT_API_URL: '$PREFECT_API_URL && echo 'Testing DNS resolution...' && nslookup prefect-server.prefect.local && echo 'Testing ping...' && ping -c 3 prefect-server.prefect.local || echo 'Ping failed' && echo 'Testing port 4200 connectivity...' && nc -zv prefect-server.prefect.local 4200 || echo 'Port 4200 unreachable' && echo 'Testing HTTP request...' && curl -v --connect-timeout 5 http://prefect-server.prefect.local:4200/api/health || echo 'HTTP request failed' && echo 'Sleeping 30s...' && sleep 30 && echo 'Starting Prefect worker...' && prefect worker start --pool ecs-pool 2>&1"
                ],
                "environment": [
                    {{
                        "name": "PREFECT_API_URL",
                        "value": "http://prefect-server.prefect.local:4200/api"
                    }},
                    {{
                        "name": "CLICKHOUSE_HOST",
                        "value": "{args[1]}"
                    }},
                    {{
                        "name": "PREFECT_LOGGING_LEVEL",
                        "value": "DEBUG"
                    }}
                ],
                "logConfiguration": {{
                    "logDriver": "awslogs",
                    "options": {{
                        "awslogs-group": "{args[0]}",
                        "awslogs-region": "{aws.config.region}",
                        "awslogs-stream-prefix": "worker"
                    }}
                }}
            }}
        ]"""
    ),
)

# --- ECS Service for Prefect Worker ---
prefect_worker_service = aws.ecs.Service(
    "prefect-worker-service",
    cluster=ecs_cluster.arn,
    task_definition=prefect_worker_task.arn,
    desired_count=1,  # Start with 1 worker, can scale up later
    launch_type="FARGATE",
    enable_execute_command=True,
    network_configuration=aws.ecs.ServiceNetworkConfigurationArgs(
        assign_public_ip=associate_public_ip,  # Workers need outbound internet for packages
        subnets=[subnet_id],
        security_groups=[sg_ecs_tasks.id],
    ),
    opts=pulumi.ResourceOptions(depends_on=[prefect_service, prefect_service_discovery]),
)

# --- Additional Outputs ---
pulumi.export("ecs_cluster_name", ecs_cluster.name)
pulumi.export("prefect_service_name", prefect_service.name)
pulumi.export("prefect_worker_service_name", prefect_worker_service.name)
pulumi.export("prefect_internal_url", "http://prefect-server.prefect.local:4200")
pulumi.export("prefect_ui_info", "Access Prefect UI at http://<task-ip>:4200 (check ECS console for task IP)")
