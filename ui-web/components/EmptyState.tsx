interface EmptyStateProps {
  message: string;
}

export function EmptyState({ message }: EmptyStateProps) {
  return (
    <div className="surface-panel p-8 text-center">
      <p className="text-[var(--text-secondary)]">{message}</p>
    </div>
  );
}
