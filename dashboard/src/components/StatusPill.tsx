export function StatusPill({ status }: { status: string }) {
  return (
    <span className={`status status-${status}`}>
      <span aria-hidden="true" className="status-dot" />
      {status}
    </span>
  );
}
