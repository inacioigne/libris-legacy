import { messages } from "@/i18n/messages";

export default function LoadingItems() {
  return <div role="status" aria-busy="true" className="p-6"><span lang="pt">{messages.pt.itemLoading}</span> / <span lang="en">{messages.en.itemLoading}</span></div>;
}
