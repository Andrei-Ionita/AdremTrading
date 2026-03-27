FROM python:3.10-slim

# Set the working directory in the Docker container
WORKDIR /app

# Copy the current directory contents into the container at /app
COPY . /app

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Run app.py when the container launches
CMD streamlit run app.py --server.headless true --server.address 0.0.0.0 --server.port ${PORT:-8501}