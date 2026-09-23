export function formatRoleName(roleName: string): string {
  return roleName.replace(/([a-z])([A-Z])/g, '$1 $2')
}
