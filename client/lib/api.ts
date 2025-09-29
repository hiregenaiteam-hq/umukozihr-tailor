import axios from 'axios';

// Create base API instance
export const api = axios.create({ 
  baseURL: 'http://localhost:8000/api/v1' 
});

// Add auth interceptor to include token in requests
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Auth endpoints
export const auth = {
  signup: (email: string, password: string) => 
    api.post('/auth/signup', { email, password }),
  login: (email: string, password: string) => 
    api.post('/auth/login', { email, password })
};

// Generation endpoints
export const generation = {
  generate: (profile: any, jobs: any[]) => 
    api.post('/generate/generate', { profile, jobs, prefs: {} }),
  getStatus: (runId: string) => 
    api.get(`/generate/status/${runId}`)
};

// Profile endpoints
export const profile = {
  save: (profileData: any) => 
    api.post('/profile/profile', profileData)
};