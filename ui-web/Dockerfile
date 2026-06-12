FROM node:20-alpine

WORKDIR /app

# Copy package files
COPY package.json package-lock.json ./

# Install dependencies
RUN npm ci

# Copy source code
COPY . .

# Gate production image builds on critical hook-order violations
RUN npm run lint:hooks

# Build
RUN npm run build

# Start
CMD ["npm", "start"]
