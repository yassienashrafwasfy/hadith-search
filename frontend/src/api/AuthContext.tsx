import { createContext, useState, useCallback, useEffect, useContext, type ReactNode } from 'react';
import { API_BASE_URL } from './config';

export const useAuth = () => {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
};

export interface Annotator {
  id: number;
  username: string;
}

export interface Assignment {
  query_id: string;
  query: string;
}

interface AnnotatorResource extends Annotator {
  assignments: Assignment[];
}

const API_V1 = `${API_BASE_URL}/api/v1`;

// Errors come back as application/problem+json; `detail` is the human-readable part.
const problemDetail = async (response: Response, fallback: string): Promise<string> => {
  try {
    const problem = await response.json();
    if (Array.isArray(problem.errors) && problem.errors.length) {
      return problem.errors.map((e: { field: string; message: string }) => `${e.field}: ${e.message}`).join(', ');
    }
    return problem.detail || fallback;
  } catch {
    return fallback;
  }
};

interface AuthState {
  token: string | null;
  annotator: Annotator | null;
  assignments: Assignment[];
  loading: boolean;
}

export interface AuthContextValue extends AuthState {
  signup: (username: string, password: string) => Promise<void>;
  signin: (username: string, password: string) => Promise<void>;
  signout: () => void;
  authFetch: (url: string, options?: RequestInit) => Promise<Response>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

const TOKEN_KEY = 'annotation_token';

export const AuthProvider = ({ children }: { children: ReactNode }) => {
  const [state, setState] = useState<AuthState>({
    token: localStorage.getItem(TOKEN_KEY),
    annotator: null,
    assignments: [],
    loading: true,
  });

  const authFetch = useCallback(async (url: string, options?: RequestInit): Promise<Response> => {
    const token = localStorage.getItem(TOKEN_KEY);
    const headers = new Headers(options?.headers);
    if (token) {
      headers.set('Authorization', `Bearer ${token}`);
    }
    const response = await fetch(url, { ...options, headers });
    if (response.status === 401) {
      localStorage.removeItem(TOKEN_KEY);
      setState({ token: null, annotator: null, assignments: [], loading: false });
    }
    return response;
  }, []);

  const fetchMe = useCallback(async (token: string) => {
    try {
      const response = await fetch(`${API_V1}/annotators/me`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) {
        localStorage.removeItem(TOKEN_KEY);
        setState({ token: null, annotator: null, assignments: [], loading: false });
        return;
      }
      const me: AnnotatorResource = await response.json();
      setState({
        token,
        annotator: { id: me.id, username: me.username },
        assignments: me.assignments,
        loading: false,
      });
    } catch {
      localStorage.removeItem(TOKEN_KEY);
      setState({ token: null, annotator: null, assignments: [], loading: false });
    }
  }, []);

  useEffect(() => {
    const token = localStorage.getItem(TOKEN_KEY);
    if (token) {
      fetchMe(token);
    } else {
      setState(prev => ({ ...prev, loading: false }));
    }
  }, [fetchMe]);

  const signup = useCallback(async (username: string, password: string) => {
    const response = await fetch(`${API_V1}/annotators`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    if (!response.ok) {
      throw new Error(await problemDetail(response, 'Signup failed'));
    }
    const data: { access_token: string; annotator: AnnotatorResource } = await response.json();
    localStorage.setItem(TOKEN_KEY, data.access_token);
    setState({
      token: data.access_token,
      annotator: { id: data.annotator.id, username: data.annotator.username },
      assignments: data.annotator.assignments,
      loading: false,
    });
  }, []);

  const signin = useCallback(async (username: string, password: string) => {
    const response = await fetch(`${API_V1}/tokens`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    if (!response.ok) {
      throw new Error(await problemDetail(response, 'Signin failed'));
    }
    const data: { access_token: string; annotator: AnnotatorResource } = await response.json();
    localStorage.setItem(TOKEN_KEY, data.access_token);
    setState({
      token: data.access_token,
      annotator: { id: data.annotator.id, username: data.annotator.username },
      assignments: data.annotator.assignments,
      loading: false,
    });
  }, []);

  // Tokens are stateless, so signing out just means forgetting the token.
  const signout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    setState({ token: null, annotator: null, assignments: [], loading: false });
  }, []);

  const value: AuthContextValue = {
    ...state,
    signup,
    signin,
    signout,
    authFetch,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};
