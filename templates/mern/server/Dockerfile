FROM node:24.21.0-alpine AS base
WORKDIR /app
RUN corepack enable pnpm
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./

FROM base AS build
RUN pnpm install --frozen-lockfile
COPY tsconfig.json tsconfig.build.json ./
COPY src ./src
RUN pnpm build

FROM build AS role-matrix
COPY test ./test
ENTRYPOINT ["node", "test/role-matrix.ts"]

FROM base AS prod-deps
RUN pnpm install --frozen-lockfile --prod

FROM node:24.21.0-alpine
ENV NODE_ENV=production
WORKDIR /app
COPY --from=prod-deps /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
COPY package.json migrate-mongo-config.js ./
COPY migrations ./migrations
USER node
EXPOSE 3000
CMD ["node", "dist/main.js"]
