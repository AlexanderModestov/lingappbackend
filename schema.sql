-- LinguaMind Database Schema for Supabase
-- Run this in Supabase Dashboard → SQL Editor

-- Enable UUID extension
create extension if not exists "uuid-ossp";

-- Materials table
create table public.materials (
  id uuid primary key default uuid_generate_v4(),
  user_id uuid references auth.users(id) on delete cascade not null,
  title text not null,
  source_type text not null check (source_type in ('youtube', 'file', 'url')),
  source_url text,
  file_path text,
  processed_text text,
  processing_status text not null default 'pending' check (processing_status in ('pending', 'processing', 'completed', 'failed')),
  created_at timestamptz default now() not null
);

-- Flashcards table
create table public.flashcards (
  id uuid primary key default uuid_generate_v4(),
  material_id uuid references public.materials(id) on delete cascade not null,
  user_id uuid references auth.users(id) on delete cascade not null,
  term text not null,
  translation text not null,
  definition text,
  context_original text,
  grammar_note text,
  learning_stage int default 0 not null,
  next_review_at timestamptz default now() not null,
  created_at timestamptz default now() not null
);

-- Enable Row Level Security
alter table public.materials enable row level security;
alter table public.flashcards enable row level security;

-- Policies for materials
create policy "Users can view their own materials"
  on public.materials for select
  using (auth.uid() = user_id);

create policy "Users can insert their own materials"
  on public.materials for insert
  with check (auth.uid() = user_id);

create policy "Users can update their own materials"
  on public.materials for update
  using (auth.uid() = user_id);

create policy "Users can delete their own materials"
  on public.materials for delete
  using (auth.uid() = user_id);

-- Policies for flashcards
create policy "Users can view their own flashcards"
  on public.flashcards for select
  using (auth.uid() = user_id);

create policy "Users can insert their own flashcards"
  on public.flashcards for insert
  with check (auth.uid() = user_id);

create policy "Users can update their own flashcards"
  on public.flashcards for update
  using (auth.uid() = user_id);

create policy "Users can delete their own flashcards"
  on public.flashcards for delete
  using (auth.uid() = user_id);

-- Quizzes table
create table public.quizzes (
  id uuid primary key default uuid_generate_v4(),
  material_id uuid references public.materials(id) on delete cascade not null,
  user_id uuid references auth.users(id) on delete cascade not null,
  questions jsonb not null, -- Array of quiz questions
  score int,
  total_questions int not null,
  completed_at timestamptz,
  created_at timestamptz default now() not null
);

-- Chat messages table
create table public.chat_messages (
  id uuid primary key default uuid_generate_v4(),
  material_id uuid references public.materials(id) on delete cascade not null,
  user_id uuid references auth.users(id) on delete cascade not null,
  role text not null check (role in ('user', 'assistant')),
  content text not null,
  created_at timestamptz default now() not null
);

-- Enable RLS for new tables
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

-- Indexes for performance
create index materials_user_id_idx on public.materials(user_id);
create index flashcards_user_id_idx on public.flashcards(user_id);
create index flashcards_material_id_idx on public.flashcards(material_id);
create index flashcards_next_review_idx on public.flashcards(next_review_at);
create index quizzes_material_id_idx on public.quizzes(material_id);
create index quizzes_user_id_idx on public.quizzes(user_id);
create index chat_messages_material_id_idx on public.chat_messages(material_id);
create index chat_messages_user_id_idx on public.chat_messages(user_id);
