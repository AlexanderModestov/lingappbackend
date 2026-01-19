-- Migration: Add quizzes and chat tables
-- Run this in Supabase Dashboard → SQL Editor

-- Quizzes table
create table if not exists public.quizzes (
  id uuid primary key default uuid_generate_v4(),
  material_id uuid references public.materials(id) on delete cascade not null,
  user_id uuid references auth.users(id) on delete cascade not null,
  questions jsonb not null,
  score int,
  total_questions int not null,
  completed_at timestamptz,
  created_at timestamptz default now() not null
);

-- Chat messages table
create table if not exists public.chat_messages (
  id uuid primary key default uuid_generate_v4(),
  material_id uuid references public.materials(id) on delete cascade not null,
  user_id uuid references auth.users(id) on delete cascade not null,
  role text not null check (role in ('user', 'assistant')),
  content text not null,
  created_at timestamptz default now() not null
);

-- Enable RLS
alter table public.quizzes enable row level security;
alter table public.chat_messages enable row level security;

-- Policies for quizzes
create policy "Users can view their own quizzes"
  on public.quizzes for select
  using (auth.uid() = user_id);

create policy "Users can insert their own quizzes"
  on public.quizzes for insert
  with check (auth.uid() = user_id);

create policy "Users can update their own quizzes"
  on public.quizzes for update
  using (auth.uid() = user_id);

create policy "Users can delete their own quizzes"
  on public.quizzes for delete
  using (auth.uid() = user_id);

-- Policies for chat_messages
create policy "Users can view their own chat messages"
  on public.chat_messages for select
  using (auth.uid() = user_id);

create policy "Users can insert their own chat messages"
  on public.chat_messages for insert
  with check (auth.uid() = user_id);

create policy "Users can delete their own chat messages"
  on public.chat_messages for delete
  using (auth.uid() = user_id);

-- Indexes
create index if not exists quizzes_material_id_idx on public.quizzes(material_id);
create index if not exists quizzes_user_id_idx on public.quizzes(user_id);
create index if not exists chat_messages_material_id_idx on public.chat_messages(material_id);
create index if not exists chat_messages_user_id_idx on public.chat_messages(user_id);
