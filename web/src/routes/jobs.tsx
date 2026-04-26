import { SliceJobsList } from '@/components/print/slice-jobs-list';

export default function JobsRoute() {
  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-[28px] font-extrabold tracking-tight text-white">Jobs</h1>
      </header>
      <SliceJobsList />
    </div>
  );
}
