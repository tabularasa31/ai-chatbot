import { NotFoundContent } from "@/components/NotFoundContent";

export default function AppNotFound() {
  return <NotFoundContent moveFocus action={{ href: "/inbox", label: "Go to Inbox" }} />;
}
