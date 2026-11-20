export type PermissionInfo = {
  key: string;
  label: string;
  description: string;
  group: string;
  groupLabel: string;
};

const groups: Record<string, string> = {
  customers: "Customers",
  products: "Products",
  inventory: "Inventory",
  orders: "Orders",
  users: "Users",
  roles: "Roles",
  permissions: "Permissions",
  qa: "Quality assurance",
  reports: "Reports",
};

const definitions: Record<string, Pick<PermissionInfo, "label" | "description">> = {
  "customers:read": { label: "View customers", description: "Access customer records and details." },
  "customers:create": { label: "Create customers", description: "Add new customer records." },
  "customers:update": { label: "Edit customers", description: "Change existing customer information and contacts." },
  "products:read": { label: "View products", description: "Access the product catalog and product details." },
  "products:create": { label: "Create products", description: "Add new products to the catalog." },
  "products:update": { label: "Edit products", description: "Change product information and availability." },
  "inventory:read": { label: "View inventory", description: "View products, stock levels, and inventory activity." },
  "inventory:update": { label: "Edit inventory", description: "This access setting is registered, but no current API action checks it." },
  "inventory:adjust": { label: "Adjust inventory", description: "Record stock increases, reductions, and reorder levels." },
  "orders:read": { label: "View orders", description: "Access customer orders and their details." },
  "orders:create": { label: "Create orders", description: "Create new customer orders." },
  "orders:update": { label: "Edit orders", description: "Change order details and progress an order through its workflow." },
  "orders:cancel": { label: "Cancel orders", description: "Order cancellation currently follows the Edit orders access setting." },
  "users:read": { label: "View users", description: "View user accounts and their access details." },
  "users:create": { label: "Create users", description: "Create user accounts." },
  "users:update": { label: "Edit users", description: "Change user account details and settings." },
  "users:deactivate": { label: "Activate or deactivate users", description: "Enable or disable user accounts." },
  "users:reset_password": { label: "Reset user passwords", description: "Generate a temporary password and send reset instructions." },
  "roles:read": { label: "View roles", description: "View available roles and the access they provide." },
  "roles:assign": { label: "Assign roles", description: "Assign roles to user accounts." },
  "permissions:read": { label: "View access settings", description: "View the available permissions and access settings." },
  "permissions:manage": { label: "Customize user access", description: "Allow or block individual permissions for a user." },
  "qa:read": { label: "View quality records", description: "This access setting is registered, but no quality records API is currently available." },
  "qa:execute": { label: "Run quality checks", description: "This access setting is registered, but no quality checks API is currently available." },
  "reports:read": { label: "View reports", description: "This access setting is registered, but no reports API is currently available." },
};

function titleCase(value: string) {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, letter => letter.toUpperCase());
}

function fallbackDefinition(key: string) {
  if (key === "*") {
    return { label: "All permissions", description: "Access all available functions.", group: "general", groupLabel: "General" };
  }
  const [resource = "", action = ""] = key.split(":", 2);
  const resourceLabel = groups[resource] || titleCase(resource || "Other access");
  const actions: Record<string, string> = {
    read: "View",
    create: "Create",
    update: "Edit",
    delete: "Delete",
    manage: "Manage",
    assign: "Assign",
    execute: "Run",
    adjust: "Adjust",
    cancel: "Cancel",
  };
  const actionLabel = actions[action] || titleCase(action || "Access");
  return {
    label: `${actionLabel} ${resourceLabel.toLowerCase()}`,
    description: `Allows this person to ${actionLabel.toLowerCase()} ${resourceLabel.toLowerCase()}.`,
    group: resource || "general",
    groupLabel: groups[resource] || titleCase(resource || "General"),
  };
}

export function describePermission(key: string): PermissionInfo {
  const definition = definitions[key];
  if (!definition) return { key, ...fallbackDefinition(key) };
  const resource = key.split(":", 1)[0];
  return {
    key,
    ...definition,
    group: resource,
    groupLabel: groups[resource] || titleCase(resource),
  };
}

export function groupPermissions(keys: string[]): { key: string; label: string; permissions: PermissionInfo[] }[] {
  const grouped = new Map<string, { label: string; permissions: PermissionInfo[] }>();
  for (const key of unique(keys)) {
    const permission = describePermission(key);
    const group = grouped.get(permission.group) || { label: permission.groupLabel, permissions: [] };
    group.permissions.push(permission);
    grouped.set(permission.group, group);
  }
  return [...grouped.entries()]
    .map(([key, group]) => ({ key, ...group, permissions: group.permissions.sort((a, b) => a.label.localeCompare(b.label)) }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

function unique(values: string[]) {
  return [...new Set(values)];
}
