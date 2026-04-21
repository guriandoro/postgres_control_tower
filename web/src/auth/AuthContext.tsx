import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  apiRequest,
  getStoredToken,
  setStoredToken,
  setUnauthorizedHandler,
} from "@/api/client";
import type { TokenResponse, UserOut } from "@/api/types";

interface AuthState {
  token: string | null;
  user: UserOut | null;
  /** True until we've finished checking the stored token at boot. */
  loading: boolean;
}

interface AuthContextValue extends AuthState {
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({
    token: getStoredToken(),
    user: null,
    loading: Boolean(getStoredToken()),
  });

  const logout = useCallback(() => {
    setStoredToken(null);
    setState({ token: null, user: null, loading: false });
  }, []);

  // Wire the api client so 401 anywhere in the app drops the user.
  useEffect(() => {
    setUnauthorizedHandler(logout);
    return () => setUnauthorizedHandler(null);
  }, [logout]);

  // On boot, if we have a token, validate it and load the user.
  useEffect(() => {
    let cancelled = false;
    const token = getStoredToken();
    if (!token) {
      setState((s) => ({ ...s, loading: false }));
      return;
    }
    apiRequest<UserOut>("/api/v1/auth/me")
      .then((user) => {
        if (!cancelled) setState({ token, user, loading: false });
      })
      .catch(() => {
        if (!cancelled) {
          setStoredToken(null);
          setState({ token: null, user: null, loading: false });
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const tokenRes = await apiRequest<TokenResponse>("/api/v1/auth/login", {
      method: "POST",
      form: true,
      body: { username: email, password },
    });
    setStoredToken(tokenRes.access_token);
    const user = await apiRequest<UserOut>("/api/v1/auth/me");
    setState({ token: tokenRes.access_token, user, loading: false });
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ ...state, login, logout }),
    [state, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
