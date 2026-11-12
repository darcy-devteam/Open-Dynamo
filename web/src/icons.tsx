// A small set of line icons, drawn at 20px.
const PATHS: Record<string, string> = {
  select: 'M5 3l10 8-4.5.8L13 17l-2 1-2.5-5.2L5 15z',
  stock: 'M3 6h14v8H3z',
  flow: 'M2 10h4m8 0h4M6 6l8 8V6l-8 8z',
  variable: 'M10 4a6 6 0 1 0 0 12 6 6 0 0 0 0-12z',
  parameter: 'M10 3l7 7-7 7-7-7z',
  table: 'M3 15c3 0 3-9 7-9s3 6 7 6M3 17h14',
  note: 'M4 4h12v8l-4 4H4zM12 16v-4h4',
  frame: 'M3 5h14v10H3zM3 8h6',
  undo: 'M7 5L3 9l4 4M3 9h8a5 5 0 0 1 0 10H8',
  redo: 'M13 5l4 4-4 4M17 9H9a5 5 0 0 0 0 10h3',
  trash: 'M4 6h12M8 6V4h4v2M6 6l1 11h6l1-11',
  copy: 'M7 7h9v10H7zM4 13V3h9',
  edit: 'M4 16l1-4 8-8 3 3-8 8zM12 5l3 3',
  sun: 'M10 6a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM10 1v2M10 17v2M1 10h2M17 10h2M3.6 3.6l1.4 1.4M15 15l1.4 1.4M3.6 16.4L5 15M15 5l1.4-1.4',
  moon: 'M16 12.5A7 7 0 0 1 7.5 4a7 7 0 1 0 8.5 8.5z',
  folder: 'M2 6V4h6l2 2h8v10H2z',
  save: 'M4 3h10l3 3v11H4zM7 3v5h6V3M7 17v-5h6v5',
  plus: 'M10 4v12M4 10h12',
  grid: 'M3 3h6v6H3zM11 3h6v6h-6zM3 11h6v6H3zM11 11h6v6h-6z',
  swap: 'M4 7h12l-3-3M16 13H4l3 3',
  close: 'M5 5l10 10M15 5L5 15',
  play: 'M6 4l10 6-10 6z',
  help: 'M7.5 7.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 1-1 1.7M10 14.5v.5M10 2a8 8 0 1 0 0 16 8 8 0 0 0 0-16z',
  fit: 'M3 7V3h4M13 3h4v4M17 13v4h-4M7 17H3v-4',
};

export function Icon({ name, size = 18 }: { name: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={PATHS[name] ?? ''} />
    </svg>
  );
}
