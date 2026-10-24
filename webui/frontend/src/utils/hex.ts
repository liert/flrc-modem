/** Format hexadecimal data for display and copying without changing stored bytes. */
export function hexText(value: string | null | undefined): string {
  return (value ?? '').toUpperCase();
}
