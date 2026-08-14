import json

def get_client():
    import docker
    try:
        return docker.from_env()
    except Exception as e:
        raise Exception(f"Failed to connect to Docker daemon: {e}. Is Docker running? (Try using docker_start_daemon)")

def docker_start_daemon() -> str:
    """Starts the Docker Desktop background engine on Windows. Call this if docker fails to connect."""
    import subprocess
    import os
    try:
        docker_path = r"C:\Program Files\Docker\Docker\Docker Desktop.exe"
        if not os.path.exists(docker_path):
            return "Error: Docker Desktop executable not found at standard path."
        
        subprocess.Popen([docker_path])
        return "Docker Desktop daemon is launching. It may take 30-60 seconds for the backend engine to fully initialize."
    except Exception as e:
        return f"Failed to start Docker Desktop: {e}"

def docker_stop_daemon() -> str:
    """Stops the Docker Desktop background engine on Windows."""
    import subprocess
    try:
        subprocess.run(["taskkill", "/IM", "Docker Desktop.exe", "/F"], check=True, capture_output=True, timeout=30)
        return "Successfully closed Docker Desktop."
    except subprocess.CalledProcessError as e:
        return f"Failed to close Docker Desktop or it wasn't running. Error: {e.stderr.decode('utf-8', errors='ignore') if e.stderr else str(e)}"
    except Exception as e:
        return f"Failed to close Docker Desktop: {e}"

def docker_list_containers(show_all: bool = False) -> str:
    """Lists Docker containers. If show_all is True, includes stopped containers."""
    try:
        client = get_client()
        containers = client.containers.list(all=show_all)
        if not containers:
            return "No containers found."
        
        result = []
        for c in containers:
            # Handle image tags properly if they exist
            image_name = c.image.tags[0] if c.image.tags else getattr(c.image, 'id', 'Unknown')
            result.append({
                "id": c.short_id,
                "name": c.name,
                "image": image_name,
                "status": c.status
            })
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error listing containers: {e}"

def docker_list_images() -> str:
    """Lists downloaded Docker images."""
    try:
        client = get_client()
        images = client.images.list()
        if not images:
            return "No local images found."
        
        result = []
        for i in images:
            if i.tags:
                result.append({
                    "id": i.short_id,
                    "tags": i.tags,
                    "size_mb": round(i.attrs.get('Size', 0) / (1024 * 1024), 2)
                })
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error listing images: {e}"

def docker_start_container(name_or_id: str) -> str:
    """Starts a stopped Docker container by name or ID."""
    try:
        client = get_client()
        container = client.containers.get(name_or_id)
        container.start()
        return f"Successfully started container: {name_or_id}"
    except Exception as e:
        return f"Error starting container {name_or_id}: {e}"

def docker_stop_container(name_or_id: str) -> str:
    """Stops a running Docker container by name or ID."""
    try:
        client = get_client()
        container = client.containers.get(name_or_id)
        container.stop()
        return f"Successfully stopped container: {name_or_id}"
    except Exception as e:
        return f"Error stopping container {name_or_id}: {e}"

def docker_remove_container(name_or_id: str) -> str:
    """Removes a Docker container by name or ID (forces removal)."""
    try:
        client = get_client()
        container = client.containers.get(name_or_id)
        container.remove(force=True)
        return f"Successfully removed container: {name_or_id}"
    except Exception as e:
        return f"Error removing container {name_or_id}: {e}"

def docker_run_image(image_name: str) -> str:
    """Runs a new container from the specified image in the background (detached mode)."""
    try:
        client = get_client()
        container = client.containers.run(image_name, detach=True)
        return f"Successfully started new container from {image_name}. ID: {container.short_id}, Name: {container.name}"
    except Exception as e:
        import docker
        if isinstance(e, docker.errors.ImageNotFound):
            return f"Error: Image '{image_name}' not found locally. Please pull it first."
        return f"Error running image {image_name}: {e}"
