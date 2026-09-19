/** The address someone paid with, carried from /welcome to /login.
 *
 *  Through sessionStorage, not the query string: a URL is written to request
 *  logs and browser history, and the success URL stopped carrying the email
 *  for exactly that reason. Read once, then removed. Every access is guarded,
 *  because storage throws in some private windows — and then the field is
 *  simply not prefilled.
 */

const KEY = 'np.signupEmail'

export function rememberSignupEmail(email: string): void {
  try {
    sessionStorage.setItem(KEY, email)
  } catch {
    /* not prefilled, nothing else lost */
  }
}

export function takeSignupEmail(): string | null {
  try {
    const v = sessionStorage.getItem(KEY)
    sessionStorage.removeItem(KEY)
    return v
  } catch {
    return null
  }
}
