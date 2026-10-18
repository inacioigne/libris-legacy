"use client";
import { useState } from "react";
import Link from "next/link";
import { IoChevronDown, IoSettingsOutline } from "react-icons/io5";
import { messages, type Locale } from "@/i18n/messages";
import type { IdentityResult } from "@/server/auth/service";
import { Dropdown } from "../shared/Dropdown";
import Image from "next/image";

export default function UserDropdown({
  locale,
  result,
}: {
  locale: Locale;
  result: Extract<IdentityResult, { status: "authenticated" }>;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [isSubDropdownOpen, setIsSubDropdownOpen] = useState(false);

  const toggleDropdown = () => {
    setIsOpen(!isOpen);
    if (isOpen) {
      setIsSubDropdownOpen(false);
    }
  };

  const closeDropdown = () => {
    setIsOpen(false);
    setIsSubDropdownOpen(false);
  };

  // console.log("USER: ", result.identity.roles);

  return (
    <div className="relative ">
      <button
        onClick={toggleDropdown}
        className="dropdown-toggle flex items-center text-gray-700 dark:text-gray-400 cursor-pointer"
      >
        <Image
          src="/example/user.png"
          alt={"User"}
          width={44}
          height={44}
          className="me-3 h-11 shrink-0 overflow-hidden rounded-full object-cover"
        />
        {/* </span> */}
        <span className="me-1 block text-theme-sm font-medium">Inácio</span>
        <IoChevronDown
          className={`size-5 text-gray-500 transition-transform duration-200 dark:text-gray-400 ${
            isOpen ? "rotate-180" : ""
          }`}
        />
      </button>
      <Dropdown isOpen={isOpen} onClose={closeDropdown}>
        <div>
          <span className="block text-theme-sm font-medium text-gray-700 dark:text-gray-400">
            Inácio Oliveira
          </span>
        </div>
        <ul className="flex flex-col gap-1 border-b border-gray-200 pt-4 pb-3 dark:border-gray-800">
          {result.identity.roles.includes('libris-admin') && 
          <li>
            <Link
              href={`/${locale}/admin`}
              onClick={closeDropdown}
              className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100 hover:no-underline! focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-600 dark:text-gray-400 dark:hover:bg-gray-800"
            >
              <IoSettingsOutline
                aria-hidden="true"
                className="size-5 shrink-0"
              />
              <span>{messages[locale].adminTitle}</span>
            </Link>
          </li>}
        </ul>
        <form action="/oidc/logout" method="post">
          <input type="hidden" name="csrf" value={result.csrf} />
          <input type="hidden" name="returnTo" value={`/${locale}/opac`} />
          <button
            type="submit"
            className="mt-2 flex w-full items-center justify-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100 hover:no-underline! focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-600 dark:text-gray-400 dark:hover:bg-gray-800 cursor-pointer"
          >
            {messages[locale].logout}
          </button>
        </form>
      </Dropdown>
    </div>
  );
}
