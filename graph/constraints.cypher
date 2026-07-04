// Uniqueness constraint for File path
CREATE CONSTRAINT file_path_unique IF NOT EXISTS
FOR (f:File)
REQUIRE f.path IS UNIQUE;

// Uniqueness constraint for Function ID
CREATE CONSTRAINT function_id_unique IF NOT EXISTS
FOR (fn:Function)
REQUIRE fn.id IS UNIQUE;

// Uniqueness constraint for Class ID
CREATE CONSTRAINT class_id_unique IF NOT EXISTS
FOR (c:Class)
REQUIRE c.id IS UNIQUE;
