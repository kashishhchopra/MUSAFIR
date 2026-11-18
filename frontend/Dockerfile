# ---- build stage ----
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
# Same-origin build: API and WebSocket go through nginx (see nginx.conf), so VITE_API_BASE and
# VITE_WS_ORIGIN stay unset. Real GPS on, demo logins off; override with --build-arg for a demo.
ARG VITE_SIMULATE_GPS=false
ARG VITE_SHOW_DEMO=false
ENV VITE_SIMULATE_GPS=$VITE_SIMULATE_GPS VITE_SHOW_DEMO=$VITE_SHOW_DEMO
RUN npm run build

# ---- serve stage ----
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
