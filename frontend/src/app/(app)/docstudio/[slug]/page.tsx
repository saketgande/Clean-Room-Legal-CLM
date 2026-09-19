import { DocstudioWorkspace } from "@/features/docstudio/DocstudioWorkspace";

export default async function DocstudioPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return <DocstudioWorkspace slug={slug} />;
}
