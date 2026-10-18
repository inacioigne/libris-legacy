import { messages } from "@/i18n/messages";

export default function LoadingInstances() {
  return <div role="status" aria-busy="true" className="p-6"><span lang="pt">{messages.pt.instanceLoading}</span> / <span lang="en">{messages.en.instanceLoading}</span></div>;
}
