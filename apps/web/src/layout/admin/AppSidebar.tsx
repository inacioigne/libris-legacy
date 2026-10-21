"use client";

import { useSidebar } from "@/context/SidebarContext";
import { messages, type Locale } from "@/i18n/messages";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useId, useState, type ReactNode } from "react";
// import { GrUserAdmin } from "react-icons/gr";
import { MdExpandMore, MdOutlineDashboardCustomize } from "react-icons/md";
import { MdOutlinePersonSearch } from "react-icons/md";
import { FaGears } from "react-icons/fa6";


type MessageKey = keyof (typeof messages)["pt"];

type NavItem = {
  key: MessageKey;
  icon: ReactNode;
  path?: string;
  new?: boolean;
  target?: string;
  subItems?: {
    key: MessageKey;
    path: string;
    pro?: boolean;
    new?: boolean;
    target?: string;
  }[];
};

const navItems: NavItem[] = [
  {
    icon: <FaGears />,
    key: "adminTitle",
    path: "/admin",
  },
  {
    icon: <MdOutlineDashboardCustomize />,
    key: "cataloging",
    subItems: [
      { key: "works", path: "/admin/catalog/works" },
      { key: "instances", path: "/admin/catalog/instances" },
      { key: "newCatalogRecord", path: "/admin/catalog/works/new" },
    ],
  },
  {
    icon: <MdOutlinePersonSearch />,
    key: "authority",
    path: "/admin/authority",
    subItems: [
      { key: "authorityAgent", path: "/admin/authority" },
      { key: "newAuthority", path: "/admin/authority/new" },
    ],
  },
];

export function AppSidebar({ locale }: { locale: Locale }) {
  const pathname = usePathname();
  const t = messages[locale];
  const submenuId = useId();
  const [submenuSelection, setSubmenuSelection] = useState<{
    pathname: string;
    key: MessageKey | null;
  } | null>(null);
  const {
    isExpanded, isMobileOpen, isHovered, setIsHovered, toggleSidebar,
  } = useSidebar();
  const showLabels = isExpanded || isHovered || isMobileOpen;
  const localizedPath = (path: string) => `/${locale}${path}`;
  const isActive = (path: string, exact = false): boolean => {
    const href = localizedPath(path);
    return pathname === href || (!exact && pathname.startsWith(`${href}/`));
  };
  const activeSubmenu = navItems.find((nav) =>
    nav.subItems?.some((item) => isActive(item.path)),
  )?.key;
  const openSubmenu = submenuSelection?.pathname === pathname
    ? submenuSelection.key
    : activeSubmenu;

  const itemClassName = (active: boolean) =>
    `group flex min-h-11 w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium no-underline transition-colors hover:no-underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700 motion-reduce:transition-none dark:focus-visible:outline-teal-300 ${
      showLabels ? "justify-start" : "justify-center"
    } ${active
      ? "bg-teal-50 text-teal-800 hover:bg-teal-100 dark:bg-teal-400/15 dark:text-teal-200 dark:hover:bg-teal-400/20"
      : "text-gray-700 hover:bg-gray-100 hover:text-gray-950 dark:text-gray-300 dark:hover:bg-gray-800 dark:hover:text-white"
    }`;

  const renderIcon = (icon: ReactNode, active: boolean) => (
    <span
      aria-hidden="true"
      className={`inline-flex size-5 shrink-0 items-center justify-center transition-colors motion-reduce:transition-none [&>svg]:size-5 ${active
        ? "text-teal-700 dark:text-teal-300"
        : "text-gray-500 group-hover:text-gray-900 group-focus-visible:text-gray-900 dark:text-gray-400 dark:group-hover:text-gray-100 dark:group-focus-visible:text-gray-100"
      }`}
    >
      {icon}
    </span>
  );

  const renderMenuItems = () => (
    <ul className="flex flex-col gap-1">
      {navItems.map((nav) => {
        const active = nav.subItems
          ? nav.subItems.some((item) => isActive(item.path))
          : Boolean(nav.path && isActive(nav.path, nav.path === "/admin"));
        const expanded = showLabels && openSubmenu === nav.key;
        const controlsId = `${submenuId}-${nav.key}`;

        return (
          <li key={nav.key}>
            {nav.subItems ? (
              <>
                <button
                  type="button"
                  aria-label={t[nav.key]}
                  aria-expanded={expanded}
                  aria-controls={controlsId}
                  title={!showLabels ? t[nav.key] : undefined}
                  className={`${itemClassName(active)} cursor-pointer`}
                  onClick={() => {
                    if (!showLabels) toggleSidebar();
                    setSubmenuSelection({
                      pathname,
                      key: expanded ? null : nav.key,
                    });
                  }}
                >
                  {renderIcon(nav.icon, active)}
                  {showLabels && (
                    <>
                      <span className="min-w-0 truncate">{t[nav.key]}</span>
                      {nav.new && (
                        <span className="rounded-full bg-teal-100 px-2 py-0.5 text-xs text-teal-800 dark:bg-teal-400/20 dark:text-teal-200">
                          {t.badgeNew}
                        </span>
                      )}
                      <MdExpandMore
                        aria-hidden="true"
                        className={`ms-auto size-5 shrink-0 transition-transform motion-reduce:transition-none ${expanded ? "rotate-180" : ""}`}
                      />
                    </>
                  )}
                </button>
                <div id={controlsId} hidden={!expanded}>
                  <ul className="ms-5 mt-1 space-y-1 border-s border-gray-200 ps-4 dark:border-gray-700">
                    {nav.subItems.map((subItem) => (
                      <li key={subItem.key}>
                        <Link
                          href={localizedPath(subItem.path)}
                          target={subItem.target}
                          aria-current={isActive(subItem.path, true) ? "page" : undefined}
                          className={itemClassName(isActive(subItem.path))}
                        >
                          <span className="min-w-0 truncate">{t[subItem.key]}</span>
                          {(subItem.new || subItem.pro) && (
                            <span className="ms-auto flex shrink-0 gap-1">
                              {subItem.new && (
                                <span className="rounded-full bg-teal-100 px-2 py-0.5 text-xs text-teal-800 dark:bg-teal-400/20 dark:text-teal-200">
                                  {t.badgeNew}
                                </span>
                              )}
                              {subItem.pro && (
                                <span className="rounded-full bg-gray-200 px-2 py-0.5 text-xs text-gray-700 dark:bg-gray-700 dark:text-gray-200">
                                  {t.badgePro}
                                </span>
                              )}
                            </span>
                          )}
                        </Link>
                      </li>
                    ))}
                  </ul>
                </div>
              </>
            ) : nav.path && (
              <Link
                href={localizedPath(nav.path)}
                target={nav.target}
                aria-current={isActive(nav.path, true) ? "page" : undefined}
                aria-label={t[nav.key]}
                title={!showLabels ? t[nav.key] : undefined}
                className={itemClassName(active)}
              >
                {renderIcon(nav.icon, active)}
                {showLabels && (
                  <span className="min-w-0 truncate">{t[nav.key]}</span>
                )}
              </Link>
            )}
          </li>
        );
      })}
    </ul>
  );

  return (
    <aside
      className={`fixed top-0 left-0 z-50 flex h-full flex-col border-r border-gray-200 bg-white px-5 text-gray-900 transition-all duration-300 ease-in-out xl:mt-0 rtl:right-0 rtl:left-auto rtl:border-r-0 rtl:border-l dark:border-gray-800 dark:bg-gray-900 ${
        isExpanded || isMobileOpen ? "w-72.5" : isHovered ? "w-72.5" : "w-22.5"
      } ${
        isMobileOpen
          ? "translate-x-0"
          : "-translate-x-full rtl:translate-x-full"
      } xl:translate-x-0 xl:rtl:translate-x-0`}
      onMouseEnter={() => !isExpanded && setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      <div
        className={`flex flex-col py-8 ${
          !isExpanded && !isHovered ? "xl:justify-center" : "justify-start"
        }`}
      >
        <Link href={`/${locale}/opac`}>
          <div className="text-3xl font-bold tracking-tight dark:text-gray-100">
            libris
            <span className="text-teal-700 dark:text-teal-300">.</span>
          </div>
        </Link>

        <div className="no-scrollbar flex flex-col overflow-y-auto duration-300 ease-linear">
          <nav className="mt-4" aria-label={t.adminNavigation}>
            <div className="flex flex-col gap-4">
              {renderMenuItems()}
            </div>
          </nav>
        </div>
      </div>
    </aside>
  );
}
