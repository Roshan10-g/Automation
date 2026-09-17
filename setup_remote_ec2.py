import os
import sys
import time
from pathlib import Path
import boto3
import paramiko

AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("AWS_ACCESS_KEY", "")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY") or os.getenv("AWS_SECRET_KEY", "")
REGION = os.getenv("AWS_REGION", "ap-south-1")
INSTANCE_IP = os.getenv("EC2_HOST", "15.207.254.33")
KEY_FILE = "keka-attendance-key.pem"
BASE_DIR = Path(__file__).resolve().parent

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def run_ssh_command(ssh, cmd: str, timeout=600):
    print(f"\n[EXEC] {cmd}")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    for line in iter(stdout.readline, ""):
        safe_line = line.encode("ascii", errors="replace").decode("ascii")
        print(safe_line, end="", flush=True)
    err = stderr.read().decode("ascii", errors="replace").strip()
    if err:
        print(f"[STDERR] {err}", flush=True)
    code = stdout.channel.recv_exit_status()
    print(f"[EXIT CODE] {code}")
    return code

def main():
    print("Connecting to S3 to get download URL...")
    s3 = boto3.client(
        "s3",
        region_name=REGION,
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY
    )
    presigned_url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": "keka-deploy-roshan-1789194507", "Key": "keka_deploy.tar.gz"},
        ExpiresIn=3600
    )
    print("Presigned URL generated.")

    print(f"Connecting to {INSTANCE_IP} via SSH...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    key = paramiko.Ed25519Key.from_private_key_file(KEY_FILE)
    
    connected = False
    for attempt in range(1, 10):
        try:
            print(f"SSH connection attempt {attempt}/9...")
            ssh.connect(INSTANCE_IP, username="ubuntu", pkey=key, timeout=15)
            connected = True
            print("Connected successfully!")
            break
        except Exception as e:
            print(f"Attempt {attempt} failed: {e}. Waiting 5s...")
            time.sleep(5)
    
    if not connected:
        raise TimeoutError(f"Could not connect to {INSTANCE_IP} after 9 attempts.")

    # 1. Setup 1.5 GB swap file if not already active
    print("\n--- 1. Checking / Setting up Swap Space ---")
    run_ssh_command(ssh, """
    if ! swapon --show | grep -q "/swapfile"; then
        echo "Creating 1.5 GB swapfile..."
        sudo fallocate -l 1.5G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=1536
        sudo chmod 600 /swapfile
        sudo mkswap /swapfile
        sudo swapon /swapfile
        echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab
    fi
    free -h
    """)

    # 2. Download and unpack project archive from S3
    print("\n--- 2. Downloading & Extracting Project Archive ---")
    run_ssh_command(ssh, f"""
    mkdir -p /home/ubuntu/keka-attendance-bot
    curl -sSL "{presigned_url}" -o /home/ubuntu/keka_deploy.tar.gz
    tar -xzf /home/ubuntu/keka_deploy.tar.gz -C /home/ubuntu/keka-attendance-bot
    rm -f /home/ubuntu/keka_deploy.tar.gz
    chown -R ubuntu:ubuntu /home/ubuntu/keka-attendance-bot
    ls -la /home/ubuntu/keka-attendance-bot
    """)

    # 3. Upload updated local keka_client.py
    print("\n--- 3. Uploading Updated keka_client.py ---")
    sftp = ssh.open_sftp()
    sftp.put(str(BASE_DIR / "keka_client.py"), "/home/ubuntu/keka-attendance-bot/keka_client.py")
    sftp.close()
    run_ssh_command(ssh, "chown ubuntu:ubuntu /home/ubuntu/keka-attendance-bot/keka_client.py")

    # 4. Install OS dependencies and Python virtualenv
    print("\n--- 4. Installing Python & Virtualenv ---")
    run_ssh_command(ssh, """
    export DEBIAN_FRONTEND=noninteractive
    sudo apt-get update -y
    sudo apt-get install -y python3-pip python3-venv
    sudo -u ubuntu python3 -m venv /home/ubuntu/keka-attendance-bot/venv
    sudo -u ubuntu /home/ubuntu/keka-attendance-bot/venv/bin/pip install --no-color --progress-bar off --upgrade pip
    sudo -u ubuntu /home/ubuntu/keka-attendance-bot/venv/bin/pip install --no-color --progress-bar off -r /home/ubuntu/keka-attendance-bot/requirements.txt
    """)

    # 5. Install Playwright Chromium & dependencies
    print("\n--- 5. Installing Playwright Chromium & Dependencies ---")
    # Install OS library dependencies as root
    run_ssh_command(ssh, "sudo /home/ubuntu/keka-attendance-bot/venv/bin/playwright install-deps chromium")
    # Install Chromium browser binary as ubuntu user
    run_ssh_command(ssh, "sudo -u ubuntu /home/ubuntu/keka-attendance-bot/venv/bin/playwright install chromium")

    # 6. Test remote Keka connection
    print("\n--- 6. Testing Keka Client on Remote Linux Server ---")
    run_ssh_command(ssh, "sudo -u ubuntu /home/ubuntu/keka-attendance-bot/venv/bin/python /home/ubuntu/keka-attendance-bot/test_keka.py")

    # 7. Configure and start Systemd service
    print("\n--- 7. Setting up keka-bot.service (Systemd) ---")
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
    sftp = ssh.open_sftp()
    with sftp.file("/home/ubuntu/keka-bot.service", "w") as f:
        f.write(service_content)
    sftp.close()

    run_ssh_command(ssh, """
    sudo mv /home/ubuntu/keka-bot.service /etc/systemd/system/keka-bot.service
    sudo chmod 644 /etc/systemd/system/keka-bot.service
    sudo systemctl daemon-reload
    sudo systemctl enable --now keka-bot.service
    sleep 3
    sudo systemctl status keka-bot.service --no-pager
    """)

    ssh.close()
    print("\n>>> REMOTE DEPLOYMENT & VERIFICATION FINISHED! <<<")

if __name__ == "__main__":
    main()
