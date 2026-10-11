# Deploy scripts pass a copy mirrored into the project registry (deploy_lib.sh).
ARG BASE_IMAGE=node:20-alpine
FROM ${BASE_IMAGE} AS base
RUN apk add --no-cache libc6-compat
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

FROM base AS deps
COPY package.json package-lock.json ./
RUN npm config set fetch-timeout 600000 \
    && npm config set fetch-retries 5 \
    && npm config set fetch-retry-maxtimeout 180000 \
    && npm config set fetch-retry-mintimeout 20000 \
    && npm ci --no-audit --progress=false

FROM base AS builder
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM base AS runner
ENV NODE_ENV=production
WORKDIR /app
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/package.json ./package.json
COPY --from=deps /app/node_modules ./node_modules
RUN npm prune --omit=dev
EXPOSE 3000
CMD ["npm", "run", "start"]
