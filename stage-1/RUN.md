# Build and Run Instructions

### Building the Docker Image
```bash
docker build -t my-backend-service .
```

### Running the Docker Container
```bash
docker run -p 8080:$PORT -e PORT=8080 my-backend-service
```