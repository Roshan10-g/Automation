import os
import sys
import time
import tarfile
from pathlib import Path
import boto3
from botocore.exceptions import ClientError

AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("AWS_ACCESS_KEY", "")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY") or os.getenv("AWS_SECRET_KEY", "")
REGION = os.getenv("AWS_REGION", "ap-south-1")  # Mumbai, India
INSTANCE_TYPE = "t3.micro"

BASE_DIR = Path(__file__).resolve().parent

def print_step(step: str):
    print(f"\n{'=' * 60}\n>>> {step}\n{'=' * 60}")

def main():
    global AWS_ACCESS_KEY, AWS_SECRET_KEY
    print("=" * 60)
    print("  AWS ZERO-SSH CLOUD-INIT DEPLOYMENT (24/7)")
    print("=" * 60)

    if not AWS_ACCESS_KEY:
        AWS_ACCESS_KEY = input("Enter your AWS Access Key ID: ").strip()
    if not AWS_SECRET_KEY:
        AWS_SECRET_KEY = input("Enter your AWS Secret Access Key: ").strip()

    s3 = boto3.client(
        "s3",
        region_name=REGION,
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY
    )
    ec2_client = boto3.client(
        "ec2",
        region_name=REGION,
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY
    )
    ec2_resource = boto3.resource(
        "ec2",
        region_name=REGION,
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY
    )

    # 1. Package Project
    print_step("Step 1: Packaging Bot Code & Authenticated Browser Profile")
    tar_path = BASE_DIR / "keka_deploy.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        for item in ["config.py", "bot.py", "keka_client.py", "state_manager.py", "requirements.txt", ".env"]:
            p = BASE_DIR / item
            if p.exists():
                tar.add(str(p), arcname=item)
        profile_dir = BASE_DIR / "browser_profile"
        if profile_dir.exists():
            tar.add(str(profile_dir), arcname="browser_profile")
    
    file_size_mb = tar_path.stat().st_size / 1024 / 1024
    print(f"[SUCCESS] Archive created: {file_size_mb:.2f} MB")

    # 2. S3 Bucket & Upload
    bucket_name = f"keka-deploy-roshan-{int(time.time())}"
    print_step(f"Step 2: Uploading to Secure S3 Bucket: {bucket_name}")
    s3.create_bucket(
        Bucket=bucket_name,
        CreateBucketConfiguration={"LocationConstraint": REGION}
    )
    print(f"Bucket created. Uploading archive...")
    s3.upload_file(str(tar_path), bucket_name, "keka_deploy.tar.gz")
    tar_path.unlink()  # Clean up local archive
    print("[SUCCESS] Uploaded to S3!")

    # 3. Generate Secure Presigned Download URL (valid 24h)
    presigned_url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket_name, "Key": "keka_deploy.tar.gz"},
        ExpiresIn=86400
    )
    print("[SUCCESS] Generated secure presigned download URL for EC2.")

    # 4. Find Ubuntu AMI
    print_step("Step 3: Finding Ubuntu 24.04 LTS AMI in Mumbai")
    resp = ec2_client.describe_images(
        Owners=["099720109477"],
        Filters=[
            {"Name": "name", "Values": ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]},
            {"Name": "state", "Values": ["available"]}
        ]
    )
    images = sorted(resp["Images"], key=lambda x: x["CreationDate"], reverse=True)
    ami_id = images[0]["ImageId"]
    print(f"Selected AMI: {ami_id}")

    # 5. Security Group
    print_step("Step 4: Checking Security Group")
    vpcs = ec2_client.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
    vpc_id = vpcs["Vpcs"][0]["VpcId"] if vpcs["Vpcs"] else ec2_client.describe_vpcs()["Vpcs"][0]["VpcId"]
    sg_name = "keka-attendance-sg"
    try:
        sgs = ec2_client.describe_security_groups(Filters=[{"Name": "group-name", "Values": [sg_name]}])
        sg_id = sgs["SecurityGroups"][0]["GroupId"]
    except Exception:
        sg = ec2_client.create_security_group(GroupName=sg_name, Description="Keka Bot SG", VpcId=vpc_id)
        sg_id = sg["GroupId"]

    # 6. Prepare User Data Script
    print_step("Step 5: Generating Cloud-Init Bootstrap Script")
    user_data_script = f"""#!/bin/bash
exec > /var/log/keka-init.log 2>&1
echo "=== Starting Keka Attendance Bot Automated Cloud Bootstrap ==="

# Wait for cloud network
sleep 10

# Download project files from S3 presigned URL
mkdir -p /home/ubuntu/keka-attendance-bot
curl -sSL "{presigned_url}" -o /home/ubuntu/keka_deploy.tar.gz
tar -xzf /home/ubuntu/keka_deploy.tar.gz -C /home/ubuntu/keka-attendance-bot
rm -f /home/ubuntu/keka_deploy.tar.gz
chown -R ubuntu:ubuntu /home/ubuntu/keka-attendance-bot

# Install Ubuntu system dependencies
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3-pip python3-venv

# Set up Python virtual environment
sudo -u ubuntu python3 -m venv /home/ubuntu/keka-attendance-bot/venv
sudo -u ubuntu /home/ubuntu/keka-attendance-bot/venv/bin/pip install --upgrade pip
sudo -u ubuntu /home/ubuntu/keka-attendance-bot/venv/bin/pip install -r /home/ubuntu/keka-attendance-bot/requirements.txt

# Install Playwright browser and its Linux OS dependencies
/home/ubuntu/keka-attendance-bot/venv/bin/playwright install --with-deps chromium

# Create 24/7 systemd service
cat << 'EOF' > /etc/systemd/system/keka-bot.service
[Unit]
Description=Keka Attendance Telegram Bot (24x7)
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/keka-attendance-bot
ExecStart=/home/ubuntu/keka-attendance-bot/venv/bin/python bot.py
Restart=always
RestartSec=10
EnvironmentFile=/home/ubuntu/keka-attendance-bot/.env

[Install]
WantedBy=multi-user.target
EOF

# Reload and start service
systemctl daemon-reload
systemctl enable --now keka-bot.service

echo "=== Keka Attendance Bot 24/7 Setup Successfully Completed! ==="
"""

    # 7. Launch EC2 with User Data
    print_step("Step 6: Launching 24/7 Cloud Server with Cloud-Init")
    instances = ec2_resource.create_instances(
        ImageId=ami_id,
        InstanceType=INSTANCE_TYPE,
        SecurityGroupIds=[sg_id],
        MinCount=1,
        MaxCount=1,
        UserData=user_data_script,
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [{"Key": "Name", "Value": "Keka-Attendance-Bot-24x7"}]
            }
        ]
    )
    instance = instances[0]
    print(f"Instance launched with ID: {instance.id}. Waiting for instance state...")
    instance.wait_until_running()
    instance.reload()
    public_ip = instance.public_ip_address
    print(f"[SUCCESS] Server is RUNNING at IP: {public_ip}")

    print("\n" + "=" * 65)
    print(" 🚀 CLOUD SERVER PROVISIONED SUCCESSFULLY!")
    print("=" * 65)
    print(f"• AWS Region: {REGION} (Mumbai, India)")
    print(f"• Instance ID: {instance.id}")
    print(f"• Public IP: {public_ip}")
    print(f"• Cloud-Init is now bootstrapping Python, Playwright, and your bot in the background (~2-3 minutes).")
    print("=" * 65)

if __name__ == "__main__":
    main()
