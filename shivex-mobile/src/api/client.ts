import axios from "axios";
import { API_CONFIG } from "../constants/api";

const baseURL = API_CONFIG.DEVICE_SERVICE.replace(/:\d+$/, "");

export const api = axios.create({
  baseURL,
  timeout: 15000,
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    console.error("[shivex api]", error);
    return Promise.resolve(null);
  }
);
