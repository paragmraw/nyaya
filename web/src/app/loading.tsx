import LoadingSkeleton from "@/components/LoadingSkeleton";

// Route-segment loading boundary. The skeleton is a client component because
// it carries a component-local animation stylesheet; it renders no data and
// is cheap to hydrate.
export default function Loading() {
  return <LoadingSkeleton />;
}