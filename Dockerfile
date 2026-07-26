FROM python:3.10-slim-bookworm

ENV PLAYWRIGHT_BROWSERS_PATH=0

# Set the working directory in the Docker container
WORKDIR /app

# Copy the current directory contents into the container at /app
COPY . /app

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
RUN python -m playwright install --with-deps chromium

# Run the web app by default; the Railway worker sets POWER_READING_PROCESS=worker.
CMD if [ "$POWER_READING_PROCESS" = "worker" ]; then python -m power_reading.worker; else streamlit run app.py --server.headless true --server.address 0.0.0.0 --server.port ${PORT:-8501}; fi
