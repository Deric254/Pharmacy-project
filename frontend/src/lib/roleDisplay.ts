/**
 * Display-only formatting for role names. The seeded owner role is
 * stored as "ChemistOwner" (one word, used as a lookup key across
 * several migrations -- not something to rename at the database
 * level casually), which reads like a typo without this. Splitting on
 * capital letters also protects against the same problem if a
 * business ever names a custom role without spaces of their own.
 */
export function formatRoleName(roleName: string): string {
  return roleName.replace(/([a-z])([A-Z])/g, '$1 $2')
}
