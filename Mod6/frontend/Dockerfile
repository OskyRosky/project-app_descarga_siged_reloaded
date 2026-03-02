# Etapa 1: build del frontend
FROM node:20 as build

# Directorio de trabajo
WORKDIR /app

# Copiamos los archivos
COPY . .

# Instalamos dependencias y build del proyecto
RUN npm install
RUN npm run build

# Etapa 2: servir frontend con nginx
FROM nginx:alpine

# Copiamos los archivos del build a nginx
COPY --from=build /app/dist /usr/share/nginx/html

# Expone el puerto 80
EXPOSE 80

# Arranca nginx
CMD ["nginx", "-g", "daemon off;"]