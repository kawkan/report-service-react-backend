# Use official Python runtime as a parent image
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install any essential packages required by the backend
RUN pip install --no-cache-dir -r requirements.txt

# Copy the current directory contents into the container at /app
COPY main.py .

# Expose port 8000 to the outside world
EXPOSE 8000

# Command to run the Fastapi application using uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
