import os
import sys
import time
import tarfile
import tempfile
from pathlib import Path
import boto3
from botocore.exceptions import ClientError
import paramiko

# Configuration
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("AWS_ACCESS_KEY", "")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY") or os.getenv("AWS_SECRET_KEY", "")
REGION = os.getenv("AWS_REGION", "ap-south-1")  # Mumbai, India
KEY_NAME = "keka-attendance-key"
SG_NAME = "keka-attendance-sg"
INSTANCE_TYPE = "t3.micro"  # Free tier eligible in Mumbai

BASE_DIR = Path(__file__).resolve().parent
PEM_PATH = BASE_DIR / f"{KEY_NAME}.pem"

def print_step(step: str):
    print(f"\n{'=' * 60}\n>>> {step}\n{'=' * 60}")

def get_or_create_key_pair(ec2_client):
    # Ensure we have an Ed25519 key pair; if an existing key (any type) exists, delete and recreate
    try:
        ec2_client.describe_key_pairs(KeyNames=[KEY_NAME])
        print(f"Key pair '{KEY_NAME}' already exists. Deleting to enforce Ed25519 type.")
        ec2_client.delete_key_pair(KeyName=KEY_NAME)
    except ClientError:
        # Key does not exist, proceed to creation
        pass
    print(f"Creating new Ed25519 Key Pair: {KEY_NAME}...")
    key_pair = ec2_client.create_key_pair(KeyName=KEY_NAME, KeyType="ed25519")
    key_material = key_pair["KeyMaterial"]
    with open(PEM_PATH, "w", encoding="utf-8") as f:
        f.write(key_material)
    print(f"[SUCCESS] Saved private key to: {PEM_PATH}")
    os.chmod(PEM_PATH, 0o600)
    print("[SECURITY] Set private key permissions to 600")
    return KEY_NAME

def get_or_create_security_group(ec2_client, ec2_resource):
    # Get default VPC
    vpcs = ec2_client.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
    if not vpcs["Vpcs"]:
        vpcs = ec2_client.describe_vpcs()
    vpc_id = vpcs["Vpcs"][0]["VpcId"]
    print(f"Using VPC: {vpc_id}")

    try:
        sgs = ec2_client.describe_security_groups(
            Filters=[
                {"Name": "group-name", "Values": [SG_NAME]},
                {"Name": "vpc-id", "Values": [vpc_id]}
            ]
        )
        if sgs["SecurityGroups"]:
            sg_id = sgs["SecurityGroups"][0]["GroupId"]
            print(f"Found existing Security Group: {sg_id}")
            return sg_id
    except ClientError:
        pass

    print(f"Creating Security Group '{SG_NAME}' in VPC {vpc_id}...")
    sg = ec2_client.create_security_group(
        GroupName=SG_NAME,
        Description="Security Group for Keka Attendance Bot",
        VpcId=vpc_id
    )
    sg_id = sg["GroupId"]

    # Inbound SSH rule
    ec2_client.authorize_security_group_ingress(
        GroupId=sg_id,
        IpPermissions=[
            {
                "IpProtocol": "tcp",
                "FromPort": 22,
                "ToPort": 22,
                "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "SSH access"}]
            }
        ]
    )
    print(f"[SUCCESS] Security Group created: {sg_id}")
    return sg_id

def find_latest_ubuntu_ami(ec2_client):
    print("Searching for latest Ubuntu 24.04 LTS AMI in ap-south-1...")
    filters = [
        {"Name": "name", "Values": ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]},
        {"Name": "state", "Values": ["available"]}
    ]
    resp = ec2_client.describe_images(Owners=["099720109477"], Filters=filters)
    images = sorted(resp["Images"], key=lambda x: x["CreationDate"], reverse=True)
    if not images:
        raise RuntimeError("No Ubuntu 24.04 AMI found in region.")
    ami_id = images[0]["ImageId"]
    print(f"Selected AMI: {ami_id} ({images[0]['Name']})")
    return ami_id

def launch_instance(ec2_resource, ami_id, sg_id, key_name):
    print(f"Launching {INSTANCE_TYPE} EC2 instance...")
    instances = ec2_resource.create_instances(
        ImageId=ami_id,
        InstanceType=INSTANCE_TYPE,
        KeyName=key_name,
        SecurityGroupIds=[sg_id],
        MinCount=1,
        MaxCount=1,
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [{"Key": "Name", "Value": "Keka-Attendance-Bot-24x7"}]
            }
        ]
    )
    instance = instances[0]
    print(f"Instance created with ID: {instance.id}. Waiting for instance to start running...")
    instance.wait_until_running()
    instance.reload()
    print(f"[SUCCESS] Instance is RUNNING! Public IP: {instance.public_ip_address}")
    return instance

def wait_for_ssh(ip: str, key_path: Path, max_retries=30, delay=10):
    print_step(f"Waiting for SSH service to become ready on {ip}...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    key = paramiko.Ed25519Key.from_private_key_file(str(key_path))

    for i in range(max_retries):
        try:
            print(f"[{i+1}/{max_retries}] Attempting SSH connection to {ip}...")
            ssh.connect(ip, username="ubuntu", pkey=key, timeout=10)
            print("[SUCCESS] Connected to remote instance via SSH!")
            return ssh
        except Exception as e:
            time.sleep(delay)
    raise TimeoutError("SSH connection timed out after multiple retries.")

def run_ssh_command(ssh, cmd: str):
    print(f"\n[REMOTE EXEC] {cmd}")
    stdin, stdout, stderr = ssh.exec_command(cmd)
    
    # Stream output
    for line in iter(stdout.readline, ""):
        print(line, end="")
    err_content = stderr.read().decode().strip()
    if err_content:
        # Some installers output warnings to stderr, which is normal
        print(f"[NOTE] {err_content}")
    exit_code = stdout.channel.recv_exit_status()
    if exit_code != 0:
        print(f"[WARN] Command returned non-zero exit status: {exit_code}")
    return exit_code

def upload_project(ssh, local_dir: Path):
    print_step("Packaging and transferring project files to remote server...")
    
    # Create a temporary tarball using the tempfile module to avoid Windows file‑locking issues
    import tempfile, shutil
    with tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz") as tmp:
        tar_path = Path(tmp.name)
    try:
        with tarfile.open(tar_path, "w:gz") as tar:
            for item in ["config.py", "bot.py", "keka_client.py", "state_manager.py", "requirements.txt", ".env"]:
                p = local_dir / item
                if p.exists():
                    tar.add(str(p), arcname=item)
            # Ensure .env is included; abort if missing
            if not (local_dir / ".env").exists():
                raise FileNotFoundError("`.env` file not found in project directory. Cannot deploy without credentials.")
            # Add browser profile if present
            profile_dir = local_dir / "browser_profile"
            if profile_dir.exists():
                tar.add(str(profile_dir), arcname="browser_profile")
        print(f"Archive created: {tar_path.stat().st_size / 1024 / 1024:.2f} MB. Uploading...")
        
        sftp = ssh.open_sftp()
        remote_tar = "/home/ubuntu/project.tar.gz"
        sftp.put(str(tar_path), remote_tar)
        sftp.close()
    finally:
        # Ensure the temporary file is removed even if an error occurs
        if tar_path.exists():
            try:
                tar_path.unlink()
            except Exception as e:
                print(f"[WARN] Failed to delete temporary archive: {e}")
    print("[SUCCESS] Project archive uploaded successfully!")

    # Extract on remote
    run_ssh_command(ssh, "mkdir -p /home/ubuntu/keka-attendance-bot")
    run_ssh_command(ssh, f"tar -xzf {remote_tar} -C /home/ubuntu/keka-attendance-bot")
    run_ssh_command(ssh, f"rm -f {remote_tar}")
    print("[SUCCESS] Project extracted to /home/ubuntu/keka-attendance-bot")

def main():
    print("=" * 60)
    print("  AWS 24/7 CLOUD DEPLOYMENT FOR KEKA ATTENDANCE BOT")
    print("=" * 60)
    
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

    # 1. Key pair
    print_step("Step 1: Setting up Key Pair")
    key_name = get_or_create_key_pair(ec2_client)

    # 2. Security Group
    print_step("Step 2: Configuring Security Group (Firewall)")
    sg_id = get_or_create_security_group(ec2_client, ec2_resource)

    # 3. Find Ubuntu AMI
    print_step("Step 3: Locating Ubuntu 24.04 LTS AMI in Mumbai")
    ami_id = find_latest_ubuntu_ami(ec2_client)

    # 4. Launch EC2
    print_step("Step 4: Launching 24/7 EC2 Instance")
    instance = launch_instance(ec2_resource, ami_id, sg_id, key_name)
    public_ip = instance.public_ip_address

    # 5. Connect via SSH
    print_step("Step 5: Connecting via SSH")
    ssh = wait_for_ssh(public_ip, PEM_PATH)

    # 6. Upload Project
    print_step("Step 6: Uploading Bot & Keka Session")
    upload_project(ssh, BASE_DIR)

    # 7. Install Dependencies
    print_step("Step 7: Installing Python & Playwright Dependencies on Remote Server")
    run_ssh_command(ssh, "sudo apt-get update -y")
    run_ssh_command(ssh, "sudo apt-get install -y python3-pip python3-venv")
    run_ssh_command(ssh, "cd /home/ubuntu/keka-attendance-bot && python3 -m venv venv")
    run_ssh_command(ssh, "/home/ubuntu/keka-attendance-bot/venv/bin/pip install --upgrade pip")
    run_ssh_command(ssh, "/home/ubuntu/keka-attendance-bot/venv/bin/pip install -r /home/ubuntu/keka-attendance-bot/requirements.txt")
    run_ssh_command(ssh, "/home/ubuntu/keka-attendance-bot/venv/bin/playwright install --with-deps chromium")

    # 8. Set up Systemd Service for 24/7 Auto-Restart
    print_step("Step 8: Configuring 24/7 Systemd Service (keka-bot.service)")
    service_content = """[Unit]
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
"""
    # Write service file
    sftp = ssh.open_sftp()
    with sftp.file("/home/ubuntu/keka-bot.service", "w") as f:
        f.write(service_content)
    sftp.close()

    run_ssh_command(ssh, "sudo mv /home/ubuntu/keka-bot.service /etc/systemd/system/keka-bot.service")
    run_ssh_command(ssh, "sudo chmod 644 /etc/systemd/system/keka-bot.service")
    print("[SECURITY] Set systemd service file permissions to 644")
    run_ssh_command(ssh, "sudo systemctl daemon-reload")
    run_ssh_command(ssh, "sudo systemctl enable --now keka-bot.service")

    # 9. Check Status
    print_step("Step 9: Verifying Service Status")
    time.sleep(3)
    run_ssh_command(ssh, "sudo systemctl status keka-bot.service --no-pager")

    ssh.close()
    
    print("\n" + "=" * 65)
    print(" 🎉 DEPLOYMENT 100% COMPLETE! YOUR BOT IS RUNNING 24/7 ON AWS!")
    print("=" * 65)
    print(f"• AWS Region: {REGION} (Mumbai, India)")
    print(f"• Server IP: {public_ip}")
    print(f"• Systemd Service: keka-bot.service (Active & Enabled)")
    print("• You can now close your laptop or shut it down anytime.")
    print("• Open Telegram and send /status to your bot!")
    print("=" * 65)

if __name__ == "__main__":
    main()
