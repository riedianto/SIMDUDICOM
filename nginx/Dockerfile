FROM nginx:alpine

# Copy nginx.conf
COPY nginx.conf /etc/nginx/nginx.conf

# Setup static files directory
RUN mkdir -p /usr/share/nginx/html

# Expose HTTP, DICOM MWL (104), and DICOM Receiver (11112) ports
EXPOSE 8026 104 11112
