"use client";

import * as React from "react";
import Link from "next/link";
import { AnimatePresence, motion } from "framer-motion";
import { Menu, X } from "lucide-react";

import { cn } from "@/lib/utils";

export interface SidebarLinkItem {
  label: string;
  href: string;
  icon: React.ReactNode;
}

interface SidebarContextValue {
  open: boolean;
  setOpen: React.Dispatch<React.SetStateAction<boolean>>;
  animate: boolean;
}

const SidebarContext = React.createContext<SidebarContextValue | undefined>(undefined);

export function useSidebar() {
  const context = React.useContext(SidebarContext);

  if (!context) {
    throw new Error("useSidebar must be used within a SidebarProvider");
  }

  return context;
}

export function SidebarProvider({
  children,
  open: openProp,
  setOpen: setOpenProp,
  animate = true,
}: {
  children: React.ReactNode;
  open?: boolean;
  setOpen?: React.Dispatch<React.SetStateAction<boolean>>;
  animate?: boolean;
}) {
  const [openState, setOpenState] = React.useState(false);

  const open = openProp ?? openState;
  const setOpen = setOpenProp ?? setOpenState;

  return (
    <SidebarContext.Provider value={{ open, setOpen, animate }}>
      {children}
    </SidebarContext.Provider>
  );
}

export function Sidebar({
  children,
  open,
  setOpen,
  animate,
}: {
  children: React.ReactNode;
  open?: boolean;
  setOpen?: React.Dispatch<React.SetStateAction<boolean>>;
  animate?: boolean;
}) {
  return (
    <SidebarProvider open={open} setOpen={setOpen} animate={animate}>
      {children}
    </SidebarProvider>
  );
}

export function SidebarBody(props: React.ComponentProps<typeof motion.div>) {
  return (
    <>
      <DesktopSidebar {...props} />
      <MobileSidebar {...(props as React.ComponentProps<"div">)} />
    </>
  );
}

export function DesktopSidebar({
  className,
  children,
  ...props
}: React.ComponentProps<typeof motion.div>) {
  const { open, setOpen, animate } = useSidebar();

  return (
    <motion.div
      className={cn(
        "hidden h-full flex-shrink-0 px-3 py-3 md:flex md:flex-col",
        "bg-[radial-gradient(circle_at_top,_rgba(88,116,186,0.22),transparent_34%),linear-gradient(180deg,rgba(12,16,24,0.98),rgba(8,11,17,0.98))]",
        className
      )}
      animate={{
        width: animate ? (open ? "292px" : "76px") : "292px",
      }}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      {...props}
    >
      {children}
    </motion.div>
  );
}

export function MobileSidebar({
  className,
  children,
  ...props
}: React.ComponentProps<"div">) {
  const { open, setOpen } = useSidebar();

  return (
    <>
      <div
        className={cn(
          "flex h-14 w-full items-center justify-between border-b border-subtle px-4 md:hidden",
          "bg-[linear-gradient(180deg,rgba(12,16,24,0.98),rgba(8,11,17,0.96))]"
        )}
        {...props}
      >
        <div className="text-[12px] font-medium uppercase tracking-[0.18em] text-secondary-content">
          Agent
        </div>
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="rounded-xl border border-white/[0.08] bg-white/[0.04] p-2 text-primary-content transition hover:bg-white/[0.08]"
          aria-label="Toggle sidebar"
        >
          <Menu className="size-4" />
        </button>
      </div>

      <AnimatePresence>
        {open && (
          <>
            <motion.button
              type="button"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="fixed inset-0 z-[90] bg-black/55 md:hidden"
              onClick={() => setOpen(false)}
              aria-label="Close sidebar backdrop"
            />
            <motion.div
              initial={{ x: "-100%", opacity: 0 }}
              animate={{ x: 0, opacity: 1 }}
              exit={{ x: "-100%", opacity: 0 }}
              transition={{ duration: 0.28, ease: "easeInOut" }}
              className={cn(
                "fixed inset-y-0 left-0 z-[100] flex w-[min(88vw,22rem)] flex-col border-r border-subtle p-4 md:hidden",
                "bg-[radial-gradient(circle_at_top,_rgba(88,116,186,0.22),transparent_34%),linear-gradient(180deg,rgba(12,16,24,0.98),rgba(8,11,17,0.98))]",
                className
              )}
            >
              <div className="mb-4 flex justify-end">
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="rounded-xl border border-white/[0.08] bg-white/[0.04] p-2 text-primary-content transition hover:bg-white/[0.08]"
                  aria-label="Close sidebar"
                >
                  <X className="size-4" />
                </button>
              </div>
              {children}
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </>
  );
}

type SidebarLinkProps = Omit<
  React.ComponentProps<typeof Link>,
  "href" | "children" | "className"
> & {
  link: SidebarLinkItem;
  className?: string;
};

export function SidebarLink({
  link,
  className,
  ...props
}: SidebarLinkProps) {
  const { open, animate } = useSidebar();

  return (
    <Link
      href={link.href}
      className={cn(
        "group/sidebar flex items-center justify-start gap-3 rounded-2xl px-2.5 py-2.5 text-sm transition",
        className
      )}
      {...props}
    >
      <span className="flex-shrink-0">{link.icon}</span>
      <motion.span
        animate={{
          display: animate ? (open ? "inline-block" : "none") : "inline-block",
          opacity: animate ? (open ? 1 : 0) : 1,
        }}
        className="inline-block whitespace-pre !p-0 !m-0"
      >
        {link.label}
      </motion.span>
    </Link>
  );
}
