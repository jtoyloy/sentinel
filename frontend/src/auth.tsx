import { createContext, useContext } from "react";
export interface User {
  username: string;
  role: "ADMIN" | "ANALYST" | "VIEWER";
}
export const AuthContext = createContext<{ user: User | null }>({ user: null });
export const useAuth = () => useContext(AuthContext);
